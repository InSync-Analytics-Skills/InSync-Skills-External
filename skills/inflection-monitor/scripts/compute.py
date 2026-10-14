#!/usr/bin/env python3
"""
inflection-monitor compute engine (Path B).

Owns EVERY number in the deliverable: series alignment, growth, acceleration,
self-referenced z-scores, streaks, comp-effect flags, base-stability gates,
ranking, divergence detection, and the heatmap matrix. The model never computes,
sorts, counts, or formats a figure - it only fetches raw MCP rows into raw.json
and writes narrative prose containing no digits.

Usage
-----
  python compute.py plan  --ticker EXM --latest-quarter 1Q27 [--json]
  python compute.py build raw.json --out-dir ./out
  python compute.py schema

Design notes live in references/methodology.md. Every threshold used here is a
named constant below so the note's methodology appendix can print them verbatim.
"""

from __future__ import annotations

import argparse
import collections
import json
import math
import os
import re
import statistics
import sys
from datetime import datetime, timezone


def _utc_now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


VERSION = "4.59.0"

LOOKBACK_QUARTERS = 13
MIN_Q_FOR_RANK = 9
MIN_Q_FOR_FULL_CONF = 11
MIN_ACCEL_OBS_FOR_Z = 4
BASE_FLOOR_FRAC = 0.01
VOLATILE_BASE_RANGE = 8.0
VOLATILE_BASE_GROWTH = 200.0
Z_DISPLAY_CAP = 9.99
MATERIAL_Z = 0.75

REVENUE_DENOMINATED_PAT = re.compile(
    r"\bmargin\b|%\s*revenue|combined ratio|loss ratio|expense ratio|"
    r"operating ratio|efficiency ratio", re.I)
LARGE_MOVE_MULT = 3.0
MIN_ABS_ACCEL_PP = 1.0
MIN_ABS_ACCEL_BPS = 25.0
STRONG_Z = 1.0
MAX_STALE_QUARTERS = 0
STEP_ZERO_DELTA_FRAC = 0.6
QUANT_MIN_STEP_BPS = 50.0
QUANT_RESOLVE_MULT = 2.0
HEATMAP_QUARTERS = 8
MONITOR_N = 3
QUIET_ROSTER_N = 5
TEMPLATE_BONUS = 0.15
SEGMENT_DETAIL_PENALTY = 0.10
CONTEXT_N = 4
TOP_N = 5
DEEP_DIVE_N = 3
HEATMAP_ROWS = 8

W_UNUSUAL = 0.40
W_TIER = 0.30
W_MATERIAL = 0.20
W_CONFIDENCE = 0.10
TIER_SCORE = {1: 1.0, 2: 0.6, 3: 0.25}
CONF_SCORE = {"High": 1.0, "Moderate": 0.65, "Low": 0.3, "Withheld": 0.0}
GUIDE_TOLERANCE_PP = 1.0

SOURCE_QUALITY = {
    "financial release": ("filed", 1),
    "sec fillings": ("filed", 1),
    "sec filings": ("filed", 1),
    "other filings": ("filed", 1),
    "press release": ("filed", 1),
    "presentations": ("presented", 2),
    "calculated": ("derived", 2),
    "transcripts": ("spoken", 3),
    "transcript": ("spoken", 3),
    "sell-side/other model sources": ("modelled", 9),
    "sell side/other model sources": ("modelled", 9),
    "consensus": ("modelled", 9),
    "estimates": ("modelled", 9),
}

MODELLED_SOURCE_PAT = re.compile(
    r"sell[- ]?side|model source|consensus|estimate|broker", re.I)

Z_BINS = [
    (-99.0, -2.0, "vneg"),
    (-2.0, -1.0, "neg"),
    (-1.0, -0.5, "sneg"),
    (-0.5, 0.5, "flat"),
    (0.5, 1.0, "spos"),
    (1.0, 2.0, "pos"),
    (2.0, 99.0, "vpos"),
]

CORE_LTCODES = [
    "IS_P_101",
    "IS_P_106-M",
    "IS_P_110-M",
    "IS_P_128",
    "CF_P_108",
    "CF_P_110",
]


KEYWORD_BANKS = {
    "financial": ["revenue", "gross profit", "operating income", "net income", "eps",
                  "margin", "operating cash flow", "free cash flow", "capex",
                  "inventory", "receivable", "payable", "debt", "deferred revenue",
                  "stock based compensation"],
    "volume_price": ["units shipped", "units", "average selling price", "price",
                     "volume", "shipments", "subscribers", "users", "customers",
                     "orders", "bookings", "backlog", "billings",
                     "remaining performance obligations", "attach"],
    "operating": ["segment", "product", "service", "geography", "region", "channel",
                  "distributor", "headcount", "employees", "store", "utilization",
                  "capacity", "retention", "churn", "cost per unit"],
    "balance_returns": ["total assets", "book value", "equity", "goodwill",
                        "leverage", "net debt", "interest expense", "return on equity",
                        "return on invested capital", "return on assets",
                        "dividend", "buyback", "shares outstanding",
                        "working capital", "days"],
}

SECTOR_BANKS = {
    "machinery": ["backlog", "dealer inventory", "price realization", "sales volume",
                  "construction industries", "resource industries",
                  "energy & transportation", "machinery", "power generation",
                  "financial products", "services revenue"],
    "semis": ["wafer", "foundry", "data center gpu", "gaming gpu", "networking",
              "automotive", "asp", "die", "node", "hyperscaler", "cloud",
              "accelerator", "switch", "console", "professional visualization"],
    "software": ["arr", "net revenue retention", "seats", "cloud revenue",
                 "subscription", "license", "professional services", "rpo",
                 "billings", "gross retention", "customers over", "arpu",
                 "logo", "expansion", "renewal"],
    "restaurants": ["same store sales", "comparable sales", "traffic", "average check",
                    "restaurant margin", "restaurant level", "average unit volume", "auv",
                    "new restaurants", "restaurant openings", "digital sales", "delivery",
                    "franchise", "system-wide sales", "company-operated"],
    "retail": ["same store sales", "comparable sales", "store count", "traffic",
               "ticket", "basket", "square footage", "e-commerce", "digital sales",
               "membership", "renewal rate", "units per transaction",
               "gross margin rate", "shrink", "markdown"],
    "banks": ["net interest income", "net interest margin", "loans", "deposits",
              "provision", "charge-offs", "efficiency ratio", "tier 1",
              "fee income", "assets under management", "book value per share",
              "non-performing", "reserve", "credit costs", "spread"],
    "pharma": ["net product revenue", "product revenue", "collaboration revenue",
               "royalty", "acquired in-process", "ipr&d", "research and development",
               "cardiometabolic", "oncology", "immunology", "neuroscience",
               "volume", "price", "manufacturing", "pipeline"],
    "aerospace": ["orders", "book-to-bill", "deliveries", "services revenue", "equipment revenue",
                  "backlog", "shop visits", "spare parts", "leap", "remaining performance",
                  "defense", "commercial engines", "operating margin", "free cash flow"],
    "homebuilders": ["net new order", "net orders", "gross sales orders", "homes closed", "closings",
                     "backlog", "selling price", "asp", "cancellation", "absorption", "community count",
                     "lots owned", "home building gross", "gross margin", "inventory"],
    "solar": ["mw sold", "modules sold", "volume sold", "shipments", "asp", "average selling price",
              "bookings", "backlog", "mw produced", "production", "nameplate capacity",
              "utilization", "manufacturing tax credit", "45x", "gross margin", "capex"],
    "distribution": ["base business", "net sales", "sales centers", "locations", "price", "volume",
                     "gross margin", "equipment", "building materials", "chemicals", "inventor",
                     "operating margin", "adjusted eps"],
    "oil_services": ["well construction", "reservoir performance", "production systems", "digital",
                     "international revenue", "north america revenue", "pretax segment",
                     "ebitda margin", "backlog", "rig count", "free cash flow"],
    "medtech": ["procedures", "installed base", "system placements", "systems placed",
                "units placed", "instruments", "accessories", "recurring revenue",
                "systems revenue", "service revenue", "utilization", "leasing",
                "gross margin", "operating margin", "ous"],
    "payments": ["payment volume", "cross border volume", "processed transactions",
                 "data processing revenue", "service revenue", "international transaction",
                 "value added services", "client incentives", "revenue per transaction",
                 "service revenue yield", "take rate"],
    "reit": ["funds from operations", "ffo", "affo", "occupancy", "rent change",
             "same store noi", "net operating income", "leases commenced",
             "retention", "development starts", "stabilization", "acquisitions",
             "dispositions", "strategic capital", "cap rate", "square feet"],
    "consumer": ["organic revenue", "unit case volume", "volume growth", "price mix",
                 "price/mix", "concentrate", "gross margin", "marketing",
                 "brand", "shipments", "market share", "operating margin",
                 "free cash flow", "dividend"],
    "healthcare": ["prescriptions", "scripts", "procedures", "utilization",
                   "medical loss ratio", "members", "pipeline", "royalty",
                   "milestone", "device revenue", "pharma revenue", "generic",
                   "biosimilar", "reimbursement", "trial"],
    "e_and_p": ["crude oil production", "natural gas production", "ngl production",
                "average daily production", "wellhead revenue", "realized price",
                "hedging gain loss", "derivative settlement", "cost/boe",
                "cash operating costs", "capital expenditures", "free cash flow",
                "net debt", "proved reserves", "wells online"],
    "midstream": ["adjusted ebitda", "distributable cash flow", "distribution coverage",
                  "throughput", "gathering volumes", "processing volumes",
                  "fee based margin", "contracted revenue", "project backlog",
                  "growth capex", "maintenance capex", "leverage ratio",
                  "net debt to ebitda", "fractionation", "storage"],
    "refining": ["refining margin", "crack spread", "throughput", "capture rate",
                 "utilization", "turnaround", "yield", "renewable diesel",
                 "marketing margin", "retail fuel volumes", "distillate",
                 "gasoline", "feedstock", "opex per barrel", "capture"],
    "utilities": ["rate base", "allowed roe", "earned roe", "authorized return",
                  "regulated capex", "capital plan", "load growth",
                  "customer accounts", "retail sales", "megawatt hours",
                  "rate case", "regulatory asset", "equity issuance",
                  "financing requirement", "credit metrics"],
    "insurance": ["net premiums written", "net premiums earned", "combined ratio",
                  "loss ratio", "expense ratio", "policies in force",
                  "renewal premium", "rate increase", "retention",
                  "net investment income", "reserve development", "catastrophe losses",
                  "book value per share", "float", "underwriting profit"],
    "airlines": ["available seat miles", "revenue passenger miles", "load factor",
                 "passenger revenue", "trasm", "prasm", "casm", "fuel cost per gallon",
                 "cargo revenue", "loyalty revenue", "premium revenue", "capacity",
                 "yield", "fuel gallons", "completion factor"],
    "asset_mgmt": ["assets under management", "net inflows", "net flows", "base fees",
                   "performance fees", "fee rate", "organic growth",
                   "technology services revenue", "distribution fees", "etf",
                   "private markets", "operating margin", "compensation",
                   "fundraising", "investment advisory"],
    "telecom": ["postpaid phone net additions", "postpaid churn", "arpu", "arpa",
                "service revenue", "wireless service revenue", "broadband net additions",
                "fixed wireless", "fiber", "equipment revenue", "connections",
                "accounts", "prepaid", "capital expenditures", "free cash flow"],
    "automotive": ["vehicle sales", "unit sales", "retail sales", "wholesale",
                   "automotive revenue", "financial services revenue", "operating income",
                   "average selling price", "electrified vehicles", "hybrid",
                   "production volume", "dealer inventory", "incentives",
                   "foreign exchange", "cost reduction"],
    "industrials": ["orders", "book to bill", "backlog", "aftermarket",
                    "equipment revenue", "service revenue", "price cost",
                    "volume growth", "capacity utilization", "dealer inventory",
                    "machine sales", "installed base", "parts", "lead time",
                    "productivity"],
}

SECTOR_MATCH = [
    ("semis", r"semicon|semis|chip|hardware"),
    ("software", r"software|saas|internet|application|it services"),
    ("reit", r"\breit\b|real estate"),
    ("consumer", r"beverage|food product|household|personal product|tobacco|"
                 r"packaged food|consumer staple"),
    ("restaurants", r"restaurant|leisure facilit|food service"),
    ("retail", r"retail|consumer discretion|apparel|restaurant|grocer|staples"),
    ("insurance", r"insur|reinsur|underwrit"),
    ("banks", r"bank|thrift|consumer financ|capital market|brokerage"),
    ("medtech", r"medical (equipment|device)|\bdevices?\b|medtech|surgical"),
    ("aerospace", r"aerospace|defen[cs]e"),
    ("homebuilders", r"home ?construction|homebuild|residential construction"),
    ("solar", r"renewable energy|solar"),
    ("distribution", r"wholesale|distribut"),
    ("oil_services", r"oil and gas services|oilfield|energy equipment"),
    ("pharma", r"pharma|biotech|drug manufactur"),
    ("healthcare", r"health|medtech|life science|device|managed care"),
    ("utilities", r"utilit|electric power|water util|gas util|multi-?utilit|"
                  r"independent power|renewable electric"),
    ("airlines", r"airline|air transport|aviation"),
    ("asset_mgmt", r"asset manage|investment manage|wealth manage|"
                   r"fund manage|asset & wealth"),
    ("machinery", r"machinery|heavy equipment|construction (and|&) (farm|mining)|"
                  r"farm (and|&) heavy|agricultural machinery"),
    ("industrials", r"industrial|capital good|machinery|aerospace|defen|"
                    r"railroad|trucking|air freight|airline|marine shipping|"
                    r"logistic|building product|electrical equipment"),
    ("telecom", r"telecom|wireless carrier|cable\b|\bteleco\b"),
    ("automotive", r"automotive|automobile|auto part|auto manufactur|"
                   r"car manufactur|vehicle manufactur"),
    ("midstream", r"storage and transport|midstream|pipeline|gathering|\bmlp\b"),
    ("refining", r"refin|marketing.*fuel|fuel.*marketing"),
    ("e_and_p", r"exploration|e&p|upstream|drilling|coal"),
    ("e_and_p", r"oil and gas|(?<!renewable )\benergy\b|petroleum"),
    ("midstream", r"oil and gas|(?<!renewable )\benergy\b|petroleum"),
    ("refining", r"oil and gas|(?<!renewable )\benergy\b|petroleum"),
    ("banks", r"\bfinancial"),
]

MAX_SECTOR_BANKS = 3


def sector_banks(subindustry):
    t = (subindustry or "").lower()
    out = []
    for key, pat in SECTOR_MATCH:
        if key not in out and re.search(pat, t):
            out.append(key)
    return out[:MAX_SECTOR_BANKS]



TEMPLATE_EVIDENCE = {
    "airlines": [
        (3, r"\b[pt]?rasm\b|revenue per available seat"),
        (3, r"\bcasm\b|cost per available seat"),
        (2, r"\basms?\b|available seat mile|load factor"),
        (2, r"passenger mile yield|\brpms?\b|revenue passenger mile"),
        (1, r"passenger\s*-?\s*revenue|aircraft fuel"),
    ],
    "asset_mgmt": [
        (3, r"\baum\b|assets under management"),
        (3, r"net flows?\b|net new (assets|money)"),
        (2, r"base fee|advisory fee|performance fee"),
        (2, r"investment advisory|administration fees"),
        (1, r"\betf\b|long-?term (flows|aum)"),
    ],
    "midstream": [
        (3, r"distributable cash flow|\bdcf\b"),
        (3, r"distribution coverage|coverage ratio"),
        (2, r"gathering volumes|throughput"),
        (2, r"fee[- ]based"),
        (1, r"\bngl\b|fractionation|\btariff\b"),
    ],
    "e_and_p": [
        (3, r"average daily production|production \(mm?boe|\bboe\s*/\s*d\b"),
        (3, r"wellhead revenue|realized price|realisation per"),
        (2, r"cost\s*/\s*boe|per boe"),
        (2, r"proved reserves|wells online|drilling|completion"),
        (1, r"\bhedg"),
    ],
    "utilities": [
        (3, r"rate base"),
        (3, r"allowed (return|roe)|authorized roe|earned roe"),
        (2, r"customer accounts|retail (electric|energy|electricity) sales|"
            r"energy sales|load growth"),
        (2, r"megawatt|gigawatt|capacity factor|net generation|generation \((m|g)wh"),
        (1, r"rate case|regulatory asset"),
    ],
    "insurance": [
        (3, r"combined ratio|loss ratio"),
        (3, r"net premiums? (written|earned)"),
        (2, r"policies in force|catastrophe loss"),
        (1, r"reserve development|underwriting"),
    ],
    "industrials": [
        (3, r"operating ratio"),
        (3, r"carloads?|freight revenue|revenue per carload|\brtm\b|\bgtm"),
        (2, r"book to bill|backlog|aftermarket|installed base"),
        (2, r"fuel surcharge|fuel gallon|workforce productivity"),
        (1, r"average employees|dealer inventory|lead time"),
    ],
    "healthcare": [
        (3, r"medical (loss ratio|care ratio)|\bmlr\b|benefit ratio"),
        (3, r"\bmembership\b|members? in force|lives covered"),
        (2, r"days claims payable|\bdcp\b|medical costs? payable"),
        (2, r"\bpmpm\b|per member per month|scripts|prescriptions"),
        (1, r"medicare|medicaid|commercial risk|value[- ]based"),
    ],
    "banks": [
        (3, r"net interest (income|margin)|\bnim\b"),
        (3, r"\bdeposits?\b|gross loans"),
        (2, r"provision for credit|charge-?offs|efficiency ratio"),
        (1, r"\bcet\s?1\b|tier\s?1"),
    ],
    "software": [
        (3, r"\brpo\b|remaining performance obligation"),
        (3, r"annual recurring revenue|\barr\b"),
        (2, r"\bbillings\b"),
        (2, r"subscription (&|and) support|subscription revenue"),
        (1, r"net revenue retention|\bnrr\b|seats?\b"),
    ],
    "restaurants": [
        (3, r"restaurant[- ]?(level )?(margin|profit)|restaurants level profit"),
        (3, r"\bauv\b|average unit volumes?"),
        (2, r"^%?\s*sss\b|same[- ]store sales|comparable (restaurant )?sales"),
        (2, r"(eop|average|total number of|opened|net adds)\b.*\brestaurants?\b"),
        (1, r"^%?\s*traffic\b|\btransactions\b|average check"),
    ],
    "retail": [
        (3, r"revenue\s*/\s*store|sales\s*/\s*(average )?(gross )?square (foot|feet)"),
        (3, r"(average )?number of stores|\bstore count\b|unit count"),
        (2, r"comparable (store )?sales|same[- ]store sales"),
        (2, r"e-?\s?commerce sales|days of inventory|inventory turnover"),
        (1, r"\bticket\b|\bfootfall\b|merchandise"),
    ],
    "refining": [
        (3, r"refining (and |&\s*)?marketing margin|refining margin|crack spread"),
        (3, r"ebit\s*/\s*barrel|per barrel|\$\s*/\s*bbl|usd\s*/\s*bbl"),
        (2, r"crude throughput|throughput \(mbd\)|throughput capacity"),
        (2, r"capture rate|renewable diesel|gross margin\s*/\s*gallon"),
        (1, r"distillates?|gasolines?|petroleum coke|turnaround"),
    ],
    "telecom": [
        (3, r"net adds?\b.*(postpaid|prepaid|wireless|broadband)|"
            r"(postpaid|prepaid).*net adds?\b"),
        (3, r"\barpu\b|average revenue per (user|account|subscriber)"),
        (2, r"\bchurn\b|broadband connections|wireless service revenue"),
        (2, r"service\s*-\s*wireless revenue|retail service wireless"),
        (1, r"spectrum|\bfios\b|fixed wireless|handset upgrade"),
    ],
    "machinery": [
        (3, r"\bbacklog\b"),
        (3, r"machinery (&|and) power systems|\bme&t\b"),
        (2, r"construction industries|resource industries|energy (&|and) transportation"),
        (2, r"price realization|sales volume"),
        (1, r"dealer inventor|financial products"),
    ],
    "aerospace": [
        (3, r"book-?to-?bill|engines?\s*-\s*deliveries|shop visits"),
        (2, r"\borders\b|commercial engines|defen[cs]e"),
        (1, r"spare parts|\bleap\b|\basp\b"),
    ],
    "homebuilders": [
        (3, r"net new orders?|gross sales orders|homes? closed|cancellation rate"),
        (3, r"backlog units|absorption|community counts?|lots owned"),
        (2, r"selling price|home building|homebuilding"),
    ],
    "solar": [
        (3, r"\bmw sold\b|modules?\s*-?\s*sold|mw produced|nameplate capacity"),
        (3, r"manufacturing tax credit|\b45x\b"),
        (2, r"bookings?\b.*\(mw\)|backlog.*\(mw\)|\(gw\)"),
    ],
    "distribution": [
        (3, r"base business"),
        (2, r"sales centers?|\bbranches\b"),
        (1, r"building materials|\bequipment\b"),
    ],
    "oil_services": [
        (3, r"well construction|reservoir performance|production systems"),
        (2, r"digital & integration|pretax segment|rig count"),
        (1, r"international\s*-\s*revenue|north america\s*-\s*revenue"),
    ],
    "medtech": [
        (3, r"\bprocedures?\b"),
        (3, r"installed base|system(s)? (units )?placed|placements"),
        (2, r"instruments?\s*(/|&|and)\s*accessories|recurring revenue"),
        (1, r"systems? revenue|service revenue"),
    ],
    "pharma": [
        (3, r"net product revenue|product revenue"),
        (3, r"acquired in-?process|\bipr&d\b"),
        (2, r"collaboration|royalt"),
        (2, r"research (and|&) development|\br&d\b"),
        (1, r"cardiometabolic|oncology|immunology|neuroscience"),
    ],
    "payments": [
        (3, r"payment volume"),
        (3, r"processed transactions"),
        (2, r"cross border volume|international transaction"),
        (2, r"data processing\s*-?\s*revenue|service\s*-\s*revenue"),
        (1, r"revenue per processed transaction|service revenue yield|client incentives"),
    ],
    "reit": [
        (3, r"\bffo\b|funds from operations|\baffo\b"),
        (3, r"occupan"),
        (2, r"same store noi|net operating income|\bnoi\b"),
        (2, r"rent change|leases? commenced|development starts"),
        (1, r"cap rate|strategic capital|square feet|sq\.?\s*ft"),
    ],
    "consumer": [
        (3, r"unit case volume|case volume"),
        (3, r"price\s*/?\s*mix"),
        (2, r"organic\s*-?\s*(total )?revenue|organic revenue"),
        (2, r"concentrate"),
        (1, r"\bbrand\b|marketing|shipments"),
    ],
    "automotive": [
        (3, r"vehicle (sales volume|retail sales|production)"),
        (3, r"\basp\b.*(vehicle|overseas|japan)|average selling price"),
        (2, r"automotive\s*-\s*(revenue|ebit)|automobile revenue"),
        (2, r"production volume|units? (sold|shipped|produced)"),
        (1, r"\bdealer\b|incentives|fleet|electrified|hybrid"),
    ],
}
def _refuse_duplicate_keys(*table_names):
    try:
        with open(os.path.abspath(__file__), encoding="utf-8") as fh:
            src = fh.read()
    except OSError:
        return
    for name in table_names:
        start = src.find("\n" + name + " = {")
        if start < 0:
            continue
        end = src.find("\n}", start)
        if end < 0:
            continue
        keys = re.findall(r'^    "([^"]+)":', src[start:end], re.M)
        dupes = sorted({k for k in keys if keys.count(k) > 1})
        if dupes:
            raise SystemExit(
                "%s declares %s twice. A dict literal keeps the last and "
                "discards the first silently, so one rule set is being deleted "
                "at parse time. Merge them." % (name, ", ".join(dupes)))


_refuse_duplicate_keys("TEMPLATE_EVIDENCE", "TEMPLATE_PRIORITY",
                       "TEMPLATE_LABEL", "TEMPLATE_VALIDATED",
                       "TEMPLATE_LABEL_ONLY")

TEMPLATE_EVIDENCE = {k: [(w, re.compile(p, re.I)) for w, p in v]
                     for k, v in TEMPLATE_EVIDENCE.items()}

TEMPLATE_LABEL = {
    "airlines": "Airlines",
    "asset_mgmt": "Asset and Wealth Management",
    "midstream": "Midstream and Pipelines",
    "e_and_p": "Exploration and Production",
    "refining": "Refining and Marketing",
    "utilities": "Regulated Utility",
    "insurance": "Insurance",
    "banks": "Banking",
    "semis": "Semiconductors",
    "reit": "Real Estate (REIT)",
    "pharma": "Pharmaceuticals",
    "medtech": "Medical Devices",
    "aerospace": "Aerospace and Defense",
    "homebuilders": "Homebuilders",
    "oil_services": "Oilfield Services",
    "solar": "Solar and Renewable Equipment",
    "distribution": "Specialty Distribution",
    "machinery": "Machinery and Equipment",
    "payments": "Payments and Networks",
    "consumer": "Consumer Staples",
    "software": "Software",
    "retail": "Retail",
    "restaurants": "Restaurants",
    "healthcare": "Managed Care and Health Services",
    "industrials": "Industrials and Transport",
    "telecom": "Telecommunications",
    "automotive": "Automotive",
}

SEGMENT_SLOT_SHARE = 0.10
DIVE_MAX_RATIOS = 2
HEADLINE_MAX_PER_FAMILY = 1
HEADLINE_MAX_RATIOS = 2
MIN_TEMPLATE_EVIDENCE = 4

TEMPLATE_LABEL_ONLY = {
    "semis":    "no routing evidence and no ranking rules; reached only on the "
                "provider's classification, and then adds nothing to the order",
}

TEMPLATE_VALIDATED = {
    "airlines": "live-validated", "asset_mgmt": "live-validated", "industrials": "live-validated",
    "healthcare": "live-validated", "insurance": "live-validated", "utilities": "live-validated",
    "e_and_p": "live-validated", "midstream": "live-validated", "semis": "live-validated",
    "banks": "live-validated",
    "software": "live-validated", "retail": "live-validated", "telecom": "live-validated",
    "refining": "live-validated", "automotive": "7203 JP",
    "reit": "live-validated", "consumer": "live-validated",
    "pharma": "live-validated", "payments": "live-validated",
    "medtech": "live-validated",
    "aerospace": "live-validated", "homebuilders": "live-validated", "oil_services": "live-validated",
    "solar": "live-validated", "distribution": "live-validated",
    "machinery": "live-validated",
}

_NOTMARGIN = r"(?!.*\bmargin\b)"
TEMPLATE_PRIORITY = {
    "airlines": [
        r"\b[pt]?rasm\b|revenue per available seat",
        r"passenger mile yield",
        r"\bcasm\b.*ex-?fuel|ex-?fuel.*\bcasm\b",
        r"load factor",
        r"margin - ebit|operating margin",
        r"^fcf$|free cash flow",
        r"passenger\s*-?\s*revenue|^total revenue$|^revenue$",
    ],
    "asset_mgmt": [
        r"^net flows?\b|net new (assets|money)",
        r"\baum\b|assets under management",
        r"base fee|investment advisory",
        r"margin - ebit|operating margin",
        r"^total revenue$|^revenue$",
        r"^ocf$|operating cash flow|^fcf$",
    ],
    "midstream": [r"^" + _NOTMARGIN + r".*(adjusted ebitda|modified ebitda|\bebitda\b)",
                  r"distributable cash flow|\bdcf\b",
                  r"coverage ratio|distribution coverage",
                  r"fee[- ]based", r"^total revenue$|^revenue$",
                  r"\bmargin\b"],
    "e_and_p": [r"average daily production|total production",
                r"wellhead revenue|realized price",
                r"cost\s*/\s*boe|cash operating costs",
                r"free cash flow|\bfcf\b", r"^total revenue"],
    "utilities": [r"rate base", r"allowed (return|roe)|earned roe",
                  r"^total revenue$|^revenue$", r"^eps|earnings per share",
                  r"customer accounts|load growth"],
    "insurance": [r"combined ratio", r"net premiums? written",
                  r"loss ratio", r"policies in force"],
    "industrials": [r"operating ratio",
                    r"freight revenue|revenue per carload",
                    r"carloads?|\brtm\b|\bgtm",
                    r"^total revenue$|^revenue$",
                    r"^eps|earnings per share"],
    "healthcare": [r"medical (loss|care) ratio|\bmlr\b",
                   r"\bmembership\b",
                   r"^eps|earnings per share",
                   r"margin\s*-\s*ebit|ebit margin",
                   r"days claims payable|\bdcp\b",
                   r"^total revenue$|^revenue$"],
    "banks": [r"net interest income", r"net interest margin|\bnim\b",
              r"gross loans|^total loans", r"^total deposits",
              r"provision for credit"],
    "software": [r"current rpo|^rpo\b",
                 r"annual recurring revenue|\barr\b",
                 r"subscription (&|and) support|subscription revenue",
                 r"\bbillings\b",
                 r"margin\s*-\s*ebit|operating margin",
                 r"^fcf$|free cash flow"],
    "restaurants": [r"comparable (restaurant )?sales|same[- ]store sales|^%?\s*sss\s*$",
                    r"^%?\s*traffic\b",
                    r"price\s*/?\s*mix|menu pric",
                    r"^(adjusted )?restaurant[- ]?(level )?margin",
                    r"\bauv\b|average unit volume",
                    r"^total revenue$|^revenue$",
                    r"^eop\s*-\s*restaurants|number of restaurants",
                    r"margin\s*-\s*ebit"],
    "retail": [r"comparable (store )?sales|same[- ]store sales|^%?\s*sss\s*$",
               r"revenue\s*/\s*store|sales\s*/\s*(average )?(gross )?square",
               r"e-?\s?commerce sales",
               r"margin\s*-\s*gross profit|gross margin",
               r"days of inventory|inventory turnover",
               r"^total revenue$|^revenue$"],
    "refining": [r"refining (and |&\s*)?marketing margin|refining margin",
                 r"ebit\s*/\s*barrel",
                 r"crude throughput|throughput \(mbd\)",
                 r"revenue\s*/\s*throughput",
                 r"margin\s*-\s*ebit",
                 r"^fcf$|free cash flow"],
    "telecom": [r"net adds?\b.*postpaid|postpaid.*net adds?\b",
                r"\barpu\b",
                r"service\s*-\s*wireless revenue|retail service wireless",
                r"broadband connections",
                r"margin\s*-\s*ebitda",
                r"^fcf$|free cash flow"],
    "machinery": [r"\bbacklog\b",
                  r"^revenue$|^total revenue$|^sales and revenues",
                  r"margin\s*-\s*ebit \(ex-?\s?1x|adjusted operating margin",
                  r"^eps \(ex-?\s?1x|^adjusted eps|^diluted eps \(ex-?\s?1x",
                  r"fcf \(machinery|free cash flow \(machinery|me&t.*free cash",
                  r"(construction industries|resource industries|energy (&|and) transportation).*sales",
                  r"price realization|sales volume"],
    "aerospace": [r"^total revenue$|^revenue$|^adjusted total revenue$",
                  r"^total orders|\borders\b",
                  r"book-?to-?bill",
                  r"engines?\s*-\s*deliveries",
                  r"services?$|sales of services",
                  r"margin\s*-\s*ebit|operating margin",
                  r"eps \(ex-?\s?1x|adjusted (diluted )?eps",
                  r"adjusted fcf|free cash flow|^fcf$"],
    "homebuilders": [r"^total revenue$|^revenue$|home building\s*-\s*revenue",
                     r"net new orders?|gross sales orders",
                     r"homes? closed|closed\s*-\s*backlog",
                     r"^eop\s*-\s*backlog|backlog units",
                     r"home closed asp|selling price",
                     r"home building gm|home building\s*-\s*gross profit|gross margin",
                     r"cancellation rate",
                     r"^eps$|diluted eps|adjusted eps"],
    "solar": [r"^total revenue$|^revenue$",
              r"mw sold\s*-\s*modules|modules?\s*sold|volume sold",
              r"^gm\s*-\s*gross profit|^margin\s*-\s*gross profit|gross margin",
              r"asp.*modules|average selling price",
              r"manufacturing tax credit|\b45x\b",
              r"net bookings|backlog eop|contracts with customers",
              r"mw produced|nameplate capacity",
              r"diluted (eps|net (income|loss).*per share)|^eps$",
              r"margin\s*-\s*ebit\b",
              r"less:\s*capex|capital expenditure"],
    "distribution": [r"^total revenue$|^revenue$",
                     r"base business growth",
                     r"^%\s*price\s*-|^%\s*volume\s*-",
                     r"^margin\s*-\s*gross profit|gross margin",
                     r"total sales centers|sales centers",
                     r"margin\s*-\s*ebit|operating margin",
                     r"^adjusted eps$|eps \(ex-?\s?1x",
                     r"inventor"],
    "oil_services": [r"^total revenue$|^revenue$",
                     r"international\s*-\s*revenue|north america\s*-\s*revenue",
                     r"(well construction|reservoir performance|production systems|digital)\s*-\s*revenue",
                     r"adjusted ebitda margin|margin\s*-\s*ebitda",
                     r"margin\s*-\s*ebit|pretax",
                     r"eps \(ex-?\s?1x|adjusted (diluted )?eps",
                     r"fcf|free cash flow"],
    "medtech": [r"^total revenue$|^revenue$",
                r"procedures",
                r"installed base",
                r"system(s)? (units )?placed|placements",
                r"instruments?\s*(/|&|and)\s*accessories|recurring revenue",
                r"gross margin|margin\s*-\s*gross profit|^gm\b",
                r"margin\s*-\s*ebit|operating margin",
                r"eps \(ex-?\s?1x|adjusted (diluted )?eps"],
    "pharma": [r"^total revenue$|^revenue$",
               r"(net )?product revenue",
               r"\(adj(usted)? gross margin\s*-\s*adj(usted)? opex\)",
               r"gross margin|margin\s*-\s*gross profit|^gm\b",
               r"research (and|&) development|\br&d\b",
               r"acquired in-?process|\bipr&d\b",
               r"margin\s*-\s*ebit|operating margin",
               r"eps \(ex-?\s?1x|adjusted (diluted )?eps"],
    "payments": [r"^net revenue$|^total revenue$",
                 r"total payment volume|^%?\s*cc\s*-\s*total payment volume|payment volume",
                 r"cross border volume",
                 r"processed transactions$|^processed transactions",
                 r"data processing\s*-?\s*revenue|service\s*-\s*revenue|international transaction",
                 r"eps \(ex-?\s?1x",
                 r"opex \(ex-?\s?1x|operating expenses \(ex",
                 r"margin\s*-\s*ebit \(ex-?\s?1x"],
    "reit": [r"core ffo",
             r"\bffo\b|\baffo\b|funds from operations",
             r"occupan",
             r"same store noi|noi\s*-\s*cash|net operating income|\bnoi\b",
             r"rent change",
             r"leases? commenced|new leases|retention",
             r"development starts|stabiliz",
             r"rental income",
             r"strategic capital income",
             r"net debt|total debt"],
    "consumer": [r"organic\s*-?\s*(total )?revenue|organic revenue",
                 r"unit case volume|case volume|^%?\s*volume\s*-\s*total",
                 r"price\s*/?\s*mix\s*-\s*total|^%?\s*price\s*/?\s*mix",
                 r"margin\s*-\s*gross profit|gross margin",
                 r"margin\s*-\s*ebit|operating margin",
                 r"^total revenue|^revenue$|net operating revenue",
                 r"^fcf$|free cash flow"],
    "automotive": [r"vehicle sales volume|vehicle retail sales",
                   r"\basp\b|average selling price",
                   r"margin\s*-\s*automotive|automotive\s*-\s*ebit",
                   r"vehicle production|production volume",
                   r"automotive\s*-\s*revenue|automobile revenue",
                   r"^fcf$|free cash flow"],
}
TEMPLATE_PRIORITY = {k: [re.compile(x, re.I) for x in v]
                     for k, v in TEMPLATE_PRIORITY.items()}

SEGMENT_DETAIL_PAT = re.compile(
    r"\s-\s(west|east|north|south|northeast|northwest|southeast|southwest|"
    r"gulf|consolidated \w+|non[- ]?consolidated|"
    r"[a-z]+ segment|domestic|international|us|u\.s\.|europe|asia)\b", re.I)


BARE_PLACE_NAMES = set()


def template_rank(template, name):
    if template in ("distribution", "retail") and (name or "").strip() in BARE_PLACE_NAMES:
        return 2
    for i, pat in enumerate(TEMPLATE_PRIORITY.get(template) or []):
        if pat.search(name or ""):
            return i
    return None


COST_EVIDENCE_OK = re.compile(r"cost|casm|expense|loss|ratio|provision|charge|claims|fuel", re.I)


def resolve_template(subindustry, names, cost_names=()):
    proposed = sector_banks(subindustry)
    scores = {}
    for tmpl, rules in TEMPLATE_EVIDENCE.items():
        hits, total = [], 0
        for weight, pat in rules:
            _pool = names if COST_EVIDENCE_OK.search(pat.pattern) else \
                [n for n in names if n not in set(cost_names)]
            matched = [n for n in _pool if pat.search(n or "")]
            if matched:
                total += weight
                hits.append(pat.pattern)
        if total:
            scores[tmpl] = {"score": total, "matched": hits}
    best = max(scores.items(), key=lambda kv: (kv[1]["score"], kv[0]),
               default=(None, {"score": 0}))
    chosen = proposed[0] if proposed else None
    if best[0] and best[1]["score"] >= MIN_TEMPLATE_EVIDENCE:
        _own = (scores.get(proposed[0], {}).get("score", 0) if proposed else 0)
        if proposed and proposed[0] in TEMPLATE_LABEL_ONLY:
            _own = max(_own, MIN_TEMPLATE_EVIDENCE)
        if (not proposed or best[0] in proposed
                or best[1]["score"] >= _own + MIN_TEMPLATE_EVIDENCE):
            chosen = best[0]

    unsupported = False
    if (chosen and proposed and chosen == proposed[0]
            and chosen not in TEMPLATE_LABEL_ONLY
            and scores.get(chosen, {}).get("score", 0) == 0):
        chosen, unsupported = None, True
    if not chosen:
        relation = "proposed_unsupported" if unsupported else "none"
    elif not proposed:
        relation = "filled_gap"
    elif chosen == proposed[0]:
        relation = "agreed"
    elif chosen in proposed:
        relation = "chose_among_proposed"
    else:
        relation = "overrode"
    override = relation == "overrode"
    return {
        "insync_subindustry": subindustry,
        "proposed_from_classification": proposed,
        "evidence_scores": {k: v["score"] for k, v in sorted(scores.items())},
        "evidence_detail": scores,
        "template": chosen,
        "template_label": TEMPLATE_LABEL.get(chosen, chosen or "General"),
        "overrode_classification": override,
        "classification_relation": relation,
        "minimum_evidence": MIN_TEMPLATE_EVIDENCE,
        "note": ("The classification string proposes a template and the disclosed "
                 "line items decide it. InSync returns the same subindustry for "
                 "exploration and production companies and for midstream operators, "
                 "so the metrics a company actually publishes are the better "
                 "evidence of what it is."),
    }


def sector_bank(subindustry):
    banks = sector_banks(subindustry)
    return banks[0] if banks else None



DISPLAY_NAMES = [
    (r"^etr\b.*\(ex-?\s?1x|^etr\b.*\badj", "Adjusted Effective Tax Rate"),
    (r"^etr\b", "Effective Tax Rate"),
    (r"^international revenue$", "International Revenue (geographic)"),
    (r"^1x items\b", "Restructuring and Other One-Time Costs"),
    (r"^gm \(ex-?\s?1x items? & (?P<x>[^)]+)\)\s*-\s*gross profit \(ex-?\s?1x items? & [^)]+\)$",
     "Adjusted Gross Margin excl. {x}"),
    (r"^gm \(ex-?\s?1x items?[^)]*\).*gross profit", "Adjusted Gross Margin"),
    (r"^gm - gross profit \(ex-?\s?1x", "Adjusted Gross Margin"),
    (r"^gm - gross profit", "Gross Margin"),
    (r"^%\s*sss$", "Comparable Sales"),
    (r"^%\s*sss\s*-\s*total revenue$", "Comparable Sales"),
    (r"^%\s*sss\s*-\s*(?P<x>.+)$", "Comparable Sales - {x}"),
    (r"^(?P<m>[a-z][a-z &/-]+?) margin\s*-\s*(?P=m)$", "{m} Margin"),
    (r"^(?P<seg>[^-]+?)\s*-\s*ebit by segment$", "{seg} EBIT"),
    (r"^(?P<seg>[^-]+?)\s*-\s*ebitda \(ex-?\s?1x items?\) by segment$", "{seg} Adjusted EBITDA"),
    (r"^(?P<seg>.+?)\s+gm \(ex-?\s?1x items?\)$", "{seg} Adjusted Gross Margin"),
    (r"^%\s*cc\s*-\s*(?P<x>.+?)\s*\(ex-?\s?1x items?\)$", "{x} Growth, constant currency (adj)"),
    (r"^%\s*cc\s*-\s*(?P<x>.+)$", "{x} Growth, constant currency"),
    (r"^gm\s*-\s*(?P<seg>[^-]+?)\s*-\s*gross profit$", "{seg} Gross Margin"),
    (r"^margin - ebit \(ex-1x", "Adjusted EBIT Margin"),
    (r"^adjusted constant-dollar basis %\s*-\s*cost\s*&\s*expenses", "Adjusted Cost & Expenses Growth (constant currency)"),
    (r"^%\s*acquisitions?\s*-\s*(net |total )?revenues?$", "Acquisition Effect on Revenue"),
    (r"^%\s*fx\s*-\s*(net |total )?revenues?$", "Currency Effect on Revenue"),
    (r"^%\s*cc organic\s*-\s*(net |total )?revenues?$", "Organic Revenue Growth (constant currency)"),
    (r"^%?\s*total rpo\s*-\s*current rpo$", "Current RPO as % of Total RPO"),
    (r"^fx impact %\s*-\s*cost\s*&\s*expenses", "Currency Effect on Adjusted Cost Growth"),
    (r"^m&a impact %\s*-\s*cost\s*&\s*expenses", "M&A Effect on Adjusted Cost Growth"),
    (r"^consolidated medical (loss|care) ratio", "Medical Care Ratio"),
    (r"^days claims payable(/dcp)?$", "Days Claims Payable"),
    (r"^intake$", "Order Intake"),
    (r"^less:\s*capex as % of revenue$", "Capex as % of Revenue"),
    (r"^capex\s*-\s*%\s*revenue\s*-\s*less:?\s*capex$", "Capex as % of Revenue"),
    (r"^%?\s*base business growth\s*-\s*total revenue", "Base Business Revenue Growth"),
    (r"^us manufacturing tax credits?$", "Section 45X Credit"),
    (r"^mw sold\s*-\s*modules$", "Modules Sold"),
    (r"^change in bps\s*-\s*gross profit\s*\(ex-?\s?1x[^)]*\)$", "Change in Adjusted Gross Margin"),
    (r"^change in bps\s*-\s*gross profit$", "Change in Gross Margin"),
    (r"^change in bps\s*-\s*(?P<x>[^(]+?)\s*\(ex-?\s?1x[^)]*\)$", "Change in Adjusted {x} Margin"),
    (r"^change in bps\s*-\s*(?P<x>[^(/]+?)$", "Change in {x} Margin"),
    (r"^(?P<x>[A-Za-z][A-Za-z .&]+?)\s*-\s*growth$", "{x} Growth"),
    (r"^opened\s*-\s*stores$", "Stores Opened"),
    (r"^total average membership$", "Total Membership (quarter average)"),
    (r"^total membership$", "Total Membership (period end)"),
    (r"^margin - ebit\s*-\s*adjusted", "Adjusted EBIT Margin"),
    (r"^ebit\s*-\s*adjusted$", "Adjusted EBIT"),
    (r"^ebitda\s*-\s*adjusted$", "Adjusted EBITDA"),
    (r"^margin - ebit\b", "EBIT Margin"),
    (r"^margin - ebitda \(ex-1x", "Adjusted EBITDA Margin"),
    (r"^margin - ebitda\b", "EBITDA Margin"),
    (r"^margin - net income \(ex-1x", "Adjusted Net Margin"),
    (r"^margin - net income\b", "Net Margin"),
    (r"^eps \(ex-1x items?\b", "Adjusted EPS"),
    (r"^eps$", "Reported EPS"),
    (r"^diluted eps \(ex-?\s?1x", "Adjusted Diluted EPS"),
    (r"^basic eps \(ex-?\s?1x", "Adjusted Basic EPS"),
    (r"^diluted eps", "Diluted EPS"),
    (r"^diluted net (income|loss|income \(loss\)|\(loss\) income)\s*(\(loss\)\s*)?per share$", "Diluted EPS"),
    (r"^eps \(ex-1x items?\s*&\s*tax benefit\)$", "Adjusted EPS ex Tax Benefit"),
    (r"^basic eps", "Basic EPS"),
    (r"^ebit \(ex-1x items?\b", "Adjusted EBIT"),
    (r"^ebitda \(ex-1x items?\b", "Adjusted EBITDA"),
    (r"^gross profit \(ex-1x items?\b", "Adjusted Gross Profit"),
    (r"^opex \(ex-1x items?\b", "Adjusted Operating Expenses"),
    (r"^opex$", "Operating Expenses"),
    (r"^net income \(ex-1x items?\b", "Adjusted Net Income"),
    (r"^pbt \(ex-1x items?\b", "Adjusted Pre-Tax Profit"),
    (r"^%?\s*medical care ratio \(ex-1x", "Adjusted Medical Care Ratio"),
    (r"^%?\s*medical care ratio\s*-\s*medical costs", "Medical Care Ratio"),
    (r"^accounts receivable,? net", "Accounts Receivable"),
    (r"^days of inventory", "Inventory Days"),
    (r"^total remaining performance obligations", "Remaining Performance Obligations"),
    (r"^current - rpo|^current rpo", "Current RPO"),
    (r"^%?\s*cc\s*-\s*cross border volume\s*\(?ex", "Cross-Border Volume Growth excl. intra-Europe (constant currency)"),
    (r"^%?\s*cc\s*-\s*cross border volume", "Cross-Border Volume Growth (total, constant currency)"),
    (r"^%?\s*cc\s*-\s*total payment volume", "Payment Volume Growth (constant currency)"),
    (r"^%?\s*organic\s*-\s*total revenue", "Organic Revenue Growth"),
    (r"^%?\s*price\s*/?\s*mix\s*-\s*total revenue", "Price/Mix Contribution to Revenue"),
    (r"^%?\s*volume\s*-\s*total revenue", "Volume Contribution to Revenue"),
    (r"^%\s*price\s*-\s*total revenue", "Price Contribution to Revenue"),
    (r"^%?\s*(fx|currency)\s*-\s*total revenue", "Currency Effect on Revenue"),
    (r"^%?\s*m&a\s*-\s*total revenue", "Acquisitions and Divestitures Effect on Revenue"),
    (r"^total revenue$", "Revenue"),
    (r"^total revenue by geography", "Revenue by Geography"),
    (r"^cash & cash equivalent", "Cash and Equivalents"),
    (r"^total debt \(ex-operating lease\)", "Total Debt"),
    (r"^net debt \(ex-operating lease\)", "Net Debt"),
    (r"^ocf$", "Operating Cash Flow"),
    (r"^fcf$", "Free Cash Flow"),
    (r"^less\s*:?\s*capex.*\(machinery\s*&\s*power systems\)\s*$",
     "Capital Expenditure (Machinery & Power Systems)"),
    (r"^less\s*:?\s*capex", "Capital Expenditure"),
    (r"^sbc$", "Stock-Based Compensation"),
    (r"^etr \(ex-?\s?1x", "Adjusted Tax Rate"),
    (r"^fds$", "Diluted Shares"),
    (r"^\(adj(usted)? gross margin\s*-\s*adj(usted)? opex\)\s*/\s*(total )?revenue",
     "Adjusted Operating Margin"),
    (r"^net interest (&|and) other income\s*/\s*expense \(ex-?\s?1x",
     "Adjusted Net Interest & Other Income"),
    (r"^total payment volumes?( by region)?( \(bn\))?$", "Total Payment Volume"),
    (r"^income tax \(ex-?\s?1x", "Adjusted Income Tax"),
    (r"^etr - income tax \(adj\)", "Adjusted Tax Rate"),
    (r"^free cash flow excluding (the )?fairlife", "Free Cash Flow ex-fairlife Payment"),
    (r"^(\w+) - consolidated revenue by geography$", None),
    (r"^etr$", "Tax Rate"),
    (r"^r&d$", "R&D"),
    (r"^sg&a$", "SG&A"),
    (r"^total headcount", "Headcount"),
    (r"^total billings", "Billings"),
    (r"^total bookings", "Bookings"),
    (r"^total deferred revenue", "Deferred Revenue"),
    (r"^net interest income \(fte\)|^nii \(fte\)", "Net Interest Income"),
    (r"^net interest margin|^nim\b", "Net Interest Margin"),
    (r"^% revenue - (.+)$", None),
]
SHARE_OF_REV_PAT = re.compile(r"^%\s*revenue\s*-\s*(.+)$", re.I)
SEGMENT_SUFFIX_PAT = re.compile(r"\s*-\s*(revenue by segment|revenue|net revenue)$", re.I)



UNIT_RULES = [
    (r"\(bcfe\s*/\s*d\)", "Bcfe/d", 1e9, 2),
    (r"\(bcf\s*/\s*d\)", "Bcf/d", 1e9, 2),
    (r"\(mmcfe\s*/\s*d\)", "MMcfe/d", 1e6, 1),
    (r"\(mmcf\s*/\s*d\)", "MMcf/d", 1e6, 1),
    (r"\(mmcfe\)", "MMcfe", 1e6, 1),
    (r"\(mmcf\)", "MMcf", 1e6, 1),
    (r"\(mboe\s*/\s*d\)|\(mboepd\)", "MBoe/d", 1e3, 1),
    (r"\(tbtu\)", "TBtu", 1.0, 1),
    (r"\(mboe\s*/\s*d\)", "MBoe/d", 1e3, 1),
    (r"\(mbbls\s*/\s*d\)", "MBbls/d", 1e3, 1),
    (r"\(mbbl\s*/\s*d\)|\(mbopd\)|\(mbo\s*/\s*d\)", "MBbl/d", 1e3, 1),
    (r"\(mbd\)|\(mbpd\)", "Mbd", 1e3, 1),
    (r"\(mmbbl\s*/\s*d\)|\(mmbd\)", "MMBbl/d", 1e6, 2),
    (r"\(mmboe\)", "MMBoe", 1e6, 1),
    (r"\(mmbbls\)", "MMBbls", 1e6, 1),
    (r"\(bcfe\)", "Bcfe", 1e9, 1),
    (r"\(bcf\)", "Bcf", 1e9, 1),
    (r"\(usd\s*/\s*boe\)|per boe|/\s*boe\b", "USD/Boe", 1.0, 2),
    (r"\(gwh\)", "GWh", 1.0, 0),
    (r"\(mw\)|^mw\b|\bmw sold\b|\bmw produced\b", "MW", 1.0, 0),
    (r"\(gw\)", "GW", 1.0, 1),
    (r"\(cents?\)|\bcasm\b|\b[pt]?rasm\b|passenger mile yield|yield\s*/\s*rpm",
     "cents", 1.0, 4),
    (r"\(asms?\s*\(mn\)\)|\basms?\b\s*\(mn\)", "million seat-miles", 1e6, 0),
    (r"\brpms?\b", "revenue passenger-miles", 1.0, 0),
    (r"load factor", "%", 1.0, 1),
    (r"gallons?.*\(mn\)|\(mn\)\s*$.*gallon|gallon consumed", "million gallons", 1e6, 0),
    (r"\(mn\)|\(millions?\)", "millions", 1e6, 1),
    (r"\(bn\)|\(billions?\)", "billions", 1e9, 2),
    (r"\bgtms?\s*/\s*employee|per employee\b|/\s*employee\b", "per employee", 1.0, 2),
    (r"revenue\s*/\s*carload|per carload\b", "USD/carload", 1.0, 2),
    (r"price\s*/\s*fuel gallon|fuel price|price per gallon|"
     r"average price\s*/\s*fuel", "USD/gallon", 1.0, 2),
    (r"(revenue|income|cost|expense)s?\s*/\s*(rtm|gtm)\b", "per ton-mile", 1.0, 2),
    (r"\brtm\s*/\s*gtm\b|\b(rtm|gtm)s?\b", "ton-miles", 1.0, 2),
    (r"\(mwh\)", "MWh", 1.0, 0),
    (r"\(mw\)", "MW", 1.0, 0),
    (r"\bdays\s+(of|sales|inventory|payable|outstanding|claims)\b|"
     r"\bdays\b.*\bpayable\b|\b(dso|dio|dpo|dcp)\b|\bdays\b\s*$|^days\s+",
     "days", 1.0, 1),
    (r"\bheadcount\b|\bemployees\b|\bstore count\b|\bwells\b|"
     r"customer accounts|\baccounts\b(?!\s+(receivable|payable))|"
     r"policies in force|\bsubscribers?\b|\bmembers?\b|\bmembership\b|"
     r"lives covered|\bscripts\b|\btransactions?\b(?!\s*(revenue|fee|volume))|"
     r"\bcards?\b(?!\s*(revenue|fee))|\bdeliveries\b|\bunits? (sold|delivered)\b",
     "count", 1.0, 0),
    (r"\(in thousands?\)", "thousands", 1e3, 0),
    (r"\(in millions?\)", "millions", 1e6, 1),
]
UNIT_RULES = [(re.compile(p, re.I), label, div, dp) for p, label, div, dp in UNIT_RULES]

BASIS_QUALIFIERS = [
    (r"\(w/?\s*gross marketing\)|gross marketing",
     "includes gross marketing: purchased volumes resold, a gross rather than a "
     "net revenue presentation, so the level is not comparable to a net revenue "
     "line and the growth rate moves with marketing throughput as well"),
    (r"\(fte\)|fully taxable equivalent",
     "fully taxable equivalent: tax-exempt income grossed up to a pre-tax "
     "equivalent, so the level exceeds the reported figure"),
    (r"managed basis",
     "managed basis: securitised balances added back, so the level exceeds the "
     "balance-sheet figure"),
    (r"\(ex-?\s?1x items?\b|non-?gaap|adjusted",
     "adjusted: excludes items management designates as one-time"),
    (r"net flows?\b|net new (assets|money)\b|net adds?\b",
     "measured as a rate against the opening stock, not as growth of the flow "
     "itself: the percentage is this period's flow divided by the prior period's "
     "closing balance"),
    (r"accident year",
     "accident-year basis: excludes movement in prior years' reserves"),
    (r"^\s*(total\s+)?average\s+(membership|members|lives|enrol)",
     "average over the quarter: the membership base that earned the period's "
     "premium, not the count on the closing day"),
    (r"^\s*(total\s+)?(membership|members|covered lives)\b(?!.*average)",
     "period-end count on the closing day, not the average base that earned the "
     "period's premium"),
    (r"\bex[- ]cats?\b",
     "excludes catastrophe losses"),
]
BASIS_QUALIFIERS = [(re.compile(p, re.I), t) for p, t in BASIS_QUALIFIERS]


def basis_qualifier(name):
    for pat, text in BASIS_QUALIFIERS:
        if pat.search(name or ""):
            return text
    return None


MULTIPLE_NAME_PAT = re.compile(
    r"coverage ratio|distribution coverage|\bturns\b|times covered|"
    r"interest coverage|\bx\s*coverage", re.I)


PHYSICAL_NAME_PAT = re.compile(
    r"square f(oo|ee)t|\bsq\.?\s*ft\b|s\.uare feet|"
    r"^new leases$|\bleases (signed|commenced)\b|"
    r"\bemployees?\b|\bheadcount\b|\bcount\b|"
    r"(?<!payment )\bvolumes?\b(?<!payment volume)(?<!payment volumes)|\bthroughput\b|\bproduction\b|\bunits?\b|"
    r"customer accounts|\baccounts\b(?!\s+(receivable|payable))|"
    r"energy sales|retail\s*-?\s*sales|\bmegawatt|\bgigawatt|\bbarrels?\b|"
    r"\bshipments?\b|\bcapacity\b|policies in force|\bsubscribers?\b|"
    r"\bmembership\b|lives covered", re.I)


EXCLUSION_QUALIFIER_PAT = re.compile(
    r"\(\s*ex(?:cluding|cl\.?|-|\s)[^)]*\)"
    r"|,?\s*\bex(?:cluding|cl\.)\s+[A-Za-z0-9 .&/'-]+$"
    r"|,?\s*\bex-[A-Za-z][A-Za-z0-9-]*", re.I)


def subject_name(name):
    out = EXCLUSION_QUALIFIER_PAT.sub(" ", name or "")
    return re.sub(r"\s{2,}", " ", out).strip(" -,")


def unit_of(name, kind, is_multiple=False):
    n = subject_name(name)
    if is_multiple or MULTIPLE_NAME_PAT.search(n):
        return ("x", 1.0, 2, "multiple, from the source value suffix")
    for pat, label, div, dp in UNIT_RULES:
        if not pat.search(n):
            continue
        if kind == "ratio" and label != "%":
            continue
        return (label, div, dp, "declared in the line-item name")
    if kind == "ratio":
        return ("%", 1.0, 2, "percent, from the source value")
    if kind == "pre_growth":
        return ("%", 1.0, 1, "the source publishes this as a growth rate")
    return (None, 1.0, 2, "currency or count, magnitude suffix applied")


UNIT_IDENTITIES = [
    (r"\btrasm\b|total revenue per available seat",
     r"total revenue|^revenue$", r"\basms?\b|available seat", "USD/ASM"),
    (r"\bprasm\b|passenger revenue per available seat",
     r"passenger\s*-?\s*revenue", r"\basms?\b|available seat", "USD/ASM"),
    (r"\bcasm\b(?!.*ex-)|cost per available seat",
     r"^opex$|operating expense", r"\basms?\b|available seat", "USD/ASM"),
    (r"passenger mile yield|yield\s*/\s*rpm",
     r"passenger\s*-?\s*revenue", r"\brpms?\b|revenue passenger", "USD/RPM"),
]
UNIT_IDENTITIES = [(re.compile(a, re.I), re.compile(b, re.I), re.compile(c, re.I), lab)
                   for a, b, c, lab in UNIT_IDENTITIES]

IDENTITY_TOL = 0.02


FLOW_STOCKS = [
    (r"^net flows?\b|^net new (assets|money)\b", r"\baum\b.*eop|^aum\b|assets under management"),
    (r"^net adds?\b|^net add\b", r"^subs\b|subscribers?\b|total (broadband )?customers"),
    (r"^net new arr\b", r"^arr\b|annual recurring revenue"),
    (r"^net deposit flows?\b", r"^deposits\b|total deposits"),
]
FLOW_STOCKS = [(re.compile(a, re.I), re.compile(b, re.I)) for a, b in FLOW_STOCKS]


SEGMENT_SUFFIX = re.compile(r"\s[-–]\s([^-–]{3,60})$")
PRESENTATION_BASIS_PAT = re.compile(
    r"^(ifrs|us[- ]?gaap|gaap|non[- ]?gaap|adjusted|reported|underlying|"
    r"pro[- ]?forma|constant currency|cc|fx[- ]?neutral|organic|like[- ]for[- ]like|"
    r"continuing operations|total|consolidated|group|fte|ex[- ]?1x items?)$", re.I)
METRIC_WORD_PAT = re.compile(
    r"^(?:total\s+|net\s+|gross\s+|adjusted\s+)?(?:revenue|sales|opex|margin|"
    r"income|earnings|expenses?|costs?|ratio|eps|cash\s*flow|ebitda|ebit|profit|"
    r"fees?|capex|assets?|liabilities|equity|arpu|yield|price|volume)s?$", re.I)


def attach_segment_scope(series):
    counts = {}
    for s in series:
        m = SEGMENT_SUFFIX.search(s.name or "")
        _q = m.group(1).strip() if m else None
        if m and not METRIC_WORD_PAT.match(_q) and not PRESENTATION_BASIS_PAT.match(_q):
            counts[m.group(1).strip().lower()] = \
                counts.get(m.group(1).strip().lower(), 0) + 1
    labels = {k for k, n in counts.items() if n >= 3}
    for s in series:
        m = SEGMENT_SUFFIX.search(s.name or "")
        lab = m.group(1).strip().lower() if m else None
        s.segment_scope = lab if lab in labels else None
    return labels


def attach_flow_denominators(series):
    for flow_pat, stock_pat in FLOW_STOCKS:
        flows = [s for s in series if flow_pat.search(s.name or "")]
        if not flows:
            continue
        stocks = [s for s in series if stock_pat.search(s.name or "")]
        if not stocks:
            continue
        stock = sorted(stocks, key=lambda s: (0 if re.search(r"eop|end", s.name or "", re.I)
                                              else 1, len(s.name or "")))[0]
        for f in flows:
            if f is stock:
                continue
            f.flow_denominator = stock
            f.flow_denominator_name = stock.display
            f.flags = [x for x in f.flags if x != "base_unstable"]
            f.derive()
    return series


IDENTITY_MIN_QUARTERS = 10
IDENTITY_ROLE_TOL = 0.03


def _aligned(a, b):
    pa = {q: v for q, v in zip(a.periods, a.values) if v is not None}
    pb = {q: v for q, v in zip(b.periods, b.values) if v is not None}
    keys = [q for q in a.periods if q in pa and q in pb]
    return [pa[q] for q in keys], [pb[q] for q in keys], keys


def _dim_facts(x):
    lab = str((x.unit_info or [""])[0] or "")
    low = lab.lower()
    kind = getattr(x, "kind", None)
    if low == "%" or kind == "ratio":
        cls = "ratio"
    elif "/" in low:
        cls = "rate"
    else:
        cls = low or "absolute"
    return (cls, kind == "ratio" or "/" in lab or lab == "%")


def _dimensionally_ok(target, parts, op, facts=None):
    def f(x):
        if facts is not None:
            got = facts.get(id(x))
            if got is not None:
                return got
        return _dim_facts(x)

    if op == "add":
        return len({f(x)[0] for x in (target,) + tuple(parts)}) == 1
    return any(f(x)[1] for x in parts)


def _satisfies(target, parts, op):
    va, vb, keys = _aligned(parts[0], parts[1])
    if len(keys) < IDENTITY_MIN_QUARTERS:
        return False
    tv = {q: v for q, v in zip(target.periods, target.values) if v is not None}
    ratios = []
    for q, x, y in zip(keys, va, vb):
        if q not in tv:
            continue
        comb = (x * y) if op == "mul" else (x + y)
        if not comb:
            return False
        ratios.append(tv[q] / comb)
    if len(ratios) < IDENTITY_MIN_QUARTERS:
        return False
    mid = sorted(ratios)[len(ratios) // 2]
    if not mid:
        return False
    if not all(abs(r / mid - 1.0) <= IDENTITY_ROLE_TOL for r in ratios):
        return False
    e = math.log10(abs(mid))
    return abs(e - round(e)) <= 0.02


def detect_signal_roles(series):
    for s in series:
        s.signal_role = "primary"
        s.derived_from = None
    cand = [s for s in series
            if s.obs >= IDENTITY_MIN_QUARTERS and any(v is not None for v in s.values)]
    facts = {id(s): _dim_facts(s) for s in cand}
    _dm = {id(x): {q: v for q, v in zip(x.periods, x.values) if v is not None} for x in cand}
    _pairc = {}
    _lo, _hi = (1 - IDENTITY_ROLE_TOL) / (1 + IDENTITY_ROLE_TOL), (1 + IDENTITY_ROLE_TOL) / (1 - IDENTITY_ROLE_TOL)

    def _pair(b, c):
        k = (id(b), id(c))
        got = _pairc.get(k)
        if got is None:
            pb, pc = _dm[id(b)], _dm[id(c)]
            keys = [q for q in b.periods if q in pb and q in pc]
            got = (keys, [pb[q] for q in keys], [pc[q] for q in keys])
            _pairc[k] = got
        return got

    def _sat(t, b, c, op):
        keys, va, vb = _pair(b, c)
        if len(keys) < IDENTITY_MIN_QUARTERS:
            return False
        tv = _dm[id(t)]
        ratios = []
        r0 = None
        for q, x, y in zip(keys, va, vb):
            if q not in tv:
                continue
            comb = (x * y) if op == "mul" else (x + y)
            if not comb:
                return False
            r = tv[q] / comb
            if r0 is None:
                r0 = r
                if not r0:
                    return _satisfies(t, (b, c), op)
            else:
                q_ = r / r0
                if not (_lo <= q_ <= _hi):
                    return False
            ratios.append(r)
        if len(ratios) < IDENTITY_MIN_QUARTERS:
            return False
        mid = sorted(ratios)[len(ratios) // 2]
        if not mid:
            return False
        if not all(abs(r / mid - 1.0) <= IDENTITY_ROLE_TOL for r in ratios):
            return False
        e = math.log10(abs(mid))
        return abs(e - round(e)) <= 0.02

    for t in cand:
        found = None
        _ft = facts[id(t)]
        for i, b in enumerate(cand):
            if b is t:
                continue
            for c in cand[i + 1:]:
                if c is t:
                    continue
                _fb, _fc = facts[id(b)], facts[id(c)]
                for op in ("mul", "add"):
                    _dok = ((_fb[1] or _fc[1]) if op == "mul"
                            else (_ft[0] == _fb[0] == _fc[0]))
                    if _dok and _sat(t, b, c, op):
                        found = (b.name, c.name, op)
                        break
                if found:
                    break
            if found:
                break
        if found:
            t.signal_role = "derived"
            t.derived_from = {"parts": [found[0], found[1]],
                              "relation": "product" if found[2] == "mul" else "sum"}
    return series


def resolve_identity_units(series):
    def find(pat):
        for s in series:
            if pat.search(s.name or "") and s.latest_value is not None:
                return s
        return None

    for s in series:
        if s.latest_value in (None, 0):
            continue
        for rx, num_pat, den_pat, base_label in UNIT_IDENTITIES:
            if not rx.search(s.name or ""):
                continue
            num, den = find(num_pat), find(den_pat)
            if not (num and den) or not den.latest_value:
                break
            implied = num.latest_value / den.latest_value
            if abs(s.latest_value - implied) <= IDENTITY_TOL * abs(implied):
                s.identity_unit = "cents"
                s.identity_multiplier = 100.0
                s.identity_basis = ("%s divided by %s reproduces this line to "
                                    "within two percent, so the published value is "
                                    "in %s and the display is in cents"
                                    % (num.display, den.display, base_label))
            elif abs(s.latest_value / 100.0 - implied) <= IDENTITY_TOL * abs(implied):
                s.identity_unit = "cents"
                s.identity_multiplier = 1.0
                s.identity_basis = ("%s divided by %s confirms this line is already "
                                    "published in cents" % (num.display, den.display))
            else:
                s.identity_failed = (
                    "%s divided by %s gives %.6f against a published %.6f; no "
                    "scale reconciles them" % (num.display, den.display,
                                               implied, s.latest_value))
            break
    return series


SCHEDULE_LABEL_PAT = re.compile(
    r"^(?:(?:19|20)\d{2}|total|there?after|thereafter)\s*-\s*[A-Za-z]{1,3}$"
    r"|^[A-Za-z .]{1,12}\s*-\s*(?:19|20)\d{2}$", re.I)
PER_UNIT_NAME_PAT = re.compile(r"per\s*sq\.?\s*ft|per square f(oo|ee)t|\bpsf\b|/\s*sq\.?\s*ft",
                               re.I)
PER_UNIT_IMPLAUSIBLE = 1e5
CUMULATIVE_Q4_OVER_Q1 = 2.5

COUNT_NAME_PAT = re.compile(
    r"\b(production|produced|units?|volumes?|vehicles?|deliveries|shipments?|"
    r"subscribers?|customers?|accounts?|members?|stores?|branches|employees|"
    r"headcount|adds?|retail sales)\b", re.I)


def unit_status(name, kind, currency_votes=0, is_multiple=False, any_currency=None):
    label, div, dp, how = unit_of(name, kind, is_multiple)
    if label == "count" and currency_votes and re.search(
            r"\bemployees?\b|\bheadcount\b|\bcount\b|\bstores?\b|\bvehicles?\b|\bunits?\b",
            name or "", re.I) and not re.search(r"revenue|sales|income|fee|cost|expense|profit",
                                                name or "", re.I):
        return (None, 1.0, dp, "conflicted",
                "the name describes a count and the source value carries a currency "
                "symbol; the note does not choose between them")
    if currency_votes and re.search(r"owned\s*(?:&|and)\s*managed|operating portfolio|operating properties",
                                    name or "", re.I) \
            and not re.search(r"acqui|dispos|develop|capital|contribut|stabili|revenue|income|\bnoi\b|"
                              r"rent|cost|expense|value|invest|capex|sales|fee|cash|debt",
                              name or "", re.I):
        return (None, 1.0, dp, "conflicted",
                "the name describes a portfolio size and the source value carries a currency "
                "symbol; its siblings are square feet, so the note does not print it as money")
    if label is not None:
        return label, div, dp, "validated", how
    if currency_votes and PHYSICAL_NAME_PAT.search(name or ""):
        return (None, 1.0, dp, "conflicted",
                "the name describes a physical quantity and the source value "
                "carries a currency symbol; the note does not choose between them")
    if currency_votes:
        return ("currency", 1.0, dp, "validated", "currency symbol in the source value")
    if any_currency is False and kind == "level" and COUNT_NAME_PAT.search(name or ""):
        return ("count", 1.0, 0, "validated",
                "no currency symbol on any observation and the name describes a count")
    return (None, 1.0, dp, "unvalidated", "no unit declared in the name or the value")


def _disambiguate_displays(series):
    groups = {}
    for x in series:
        groups.setdefault(display_name(x.name), []).append(x)
    for label, xs in groups.items():
        if len(xs) < 2:
            continue
        for x in xs:
            raw = x.name or ""
            q = None
            _st = re.search(r"\s*-\s*(bs|cfo|cfi|cff|cf)\s*$", raw, re.I)
            _xa = re.search(r"\(ex-?\s?1x items?\s*(?:&|,)\s*(.+?)\)\s*"
                            r"(?:\((?:excl\.?|excluding)\s*(.+)\))?\s*$", raw, re.I)
            _by = re.search(r"\bby (segment|geography|region|product)\s*$", raw, re.I)
            ps = [p for p in re.findall(r"\(([^()]+)\)", raw)
                  if not re.search(r"ex-?\s?1x|consolidated|^%$|incl\.|excl\.|releases",
                                   p, re.I)]
            if _st:
                q = {"bs": "balance sheet", "cfi": "investing cash flow",
                     "cff": "financing cash flow"}.get(_st.group(1).lower(),
                                                       "cash-flow statement")
            elif _xa:
                _twins = [y for y in xs if y is not x and re.search(
                    r"\((?:excl\.?|excluding)\s*.+\)\s*$", y.name or "", re.I)]
                if _xa.group(2):
                    _it = re.sub(r"in-?process r\s*&\s*d", "IPR&D", _xa.group(2).strip(), flags=re.I)
                    x._display_override = label + " ex-" + _it
                    continue
                if _twins:
                    continue
                q = "also excluding " + _xa.group(1).strip()
            elif _by:
                x._display_override = label + " by " + _by.group(1).title()
                continue
            elif ps:
                q = ps[-1].strip()
            else:
                tail = re.split(r"\s+-\s+", raw)
                if (len(tail) > 1 and not re.search(r"consolidated|total|ex-?\s?1x",
                                                    tail[-1], re.I)):
                    q = tail[-1].strip()
            if q and q.lower() not in label.lower():
                x._display_override = label + " (" + q + ")"
        seen = {}
        for x in xs:
            d = x.display
            if d in seen:
                y = seen[d]
                va, vb = (x.values or [])[-8:], (y.values or [])[-8:]
                _pairs = [(a, b) for a, b in zip(va, vb) if a is not None and b is not None]
                _tol = 0.0005 * max([abs(b) for _, b in _pairs] + [1.0])
                same = (len(_pairs) >= 6
                        and sum(1 for a, b in _pairs if abs(a - b) <= _tol) >= len(_pairs) - 1)
                if not same:
                    x._display_override = x.name
            seen.setdefault(d, x)


def display_name(name):
    out = _display_name_core(name)
    if isinstance(out, str):
        out = re.sub(r"\bBackog\b", "Backlog", out)
        out = re.sub(r"\bMargin\s+Margin\b", "Margin %", out, flags=re.I)
        out = re.sub(r"\b(Revenue|Revenues)\s+\1\b", r"\1", out, flags=re.I)
    return out


def _display_name_core(name):
    raw = (name or "").strip()
    _yy = re.match(r"^\s*(?:y/y|yoy)\s*-\s*(?:total\s+)?(.+)$", raw, re.I)
    if _yy and not re.search(r"\bgrowth\b", _yy.group(1), re.I):
        _in = _yy.group(1).strip()
        if re.fullmatch(r"cost\s*&\s*expenses(\s*\(ex-?\s?1x[^)]*\))?", _in, re.I):
            return ("Adjusted " if "1x" in _in.lower() else "") + "Cost & Expenses Growth"
        return display_name(_in) + " Growth"
    _gm = re.match(r"^gm\s+(.+?)\s*-\s*\(ex-?\s?1x items?(?:\s*&\s*([^)]+))?\)", raw, re.I)
    if _gm:
        return ("Adjusted Gross Margin, " + _gm.group(1).strip()
                + (" (also excluding " + _gm.group(2).strip() + ")" if _gm.group(2) else ""))
    raw = re.sub(r"^(.{12,}?)\s+-\s+(?:fcf|ocf)\s*$", r"\1", raw, flags=re.I)
    raw = re.sub(r"\s*-\s*consolidated\s+(?:free cash flow|cash flows?(?: statement)?|"
                 r"balance sheet|income statement)\s*$", "", raw, flags=re.I) or raw
    low = raw.lower()
    m = SHARE_OF_REV_PAT.match(raw)
    if m:
        inner = re.sub(r"\s*\(ex-1x items?\)", "", m.group(1)).strip()
        inner = re.sub(r"\bpersonal cost\b", "Personnel Cost", inner, flags=re.I)
        inner = re.sub(r"\s*-\s*cost\s*&\s*expenses\b", "", inner, flags=re.I).strip()
        inner = re.sub(r"^(.{2,}?)\s*-\s*(net\s+)?revenues?$", r"\1", inner, flags=re.I).strip()
        return f"{inner.title() if inner.islower() else inner} as % of Revenue"
    for pat, label in DISPLAY_NAMES:
        if label and re.match(pat, low):
            if "{" in label:
                _m = re.match(pat, raw.strip(), re.I)
                if _m:
                    return label.format(**{k: v.strip() for k, v in _m.groupdict().items()})
                continue
            return label
    out = SEGMENT_SUFFIX_PAT.sub(" Revenue", raw)
    out = re.sub(r"\s*\(ex-1x items?\)", " (adj)", out, flags=re.I)
    out = re.sub(r"\s*-\s*bs$|\s*-\s*cfo$|\s*-\s*cfi$|\s*-\s*cff$|\s*-\s*cf$",
                 "", out, flags=re.I)

    out = re.sub(r"\bpersonal cost\b", "Personnel Cost", out, flags=re.I)
    out = re.sub(r"\s*\(%\)", "", out)
    out = re.sub(r"\s*-\s*reit\b", "", out, flags=re.I)
    if not re.search(r"(%|basis)\s*-\s*cost\s*&\s*expenses", out, re.I):
        out = re.sub(r"\s*-\s*cost\s*&\s*expenses\b", "", out, flags=re.I)
    out = re.sub(r"^global volume\s*-\s*unit case volume growth.*$",
                 "Global Unit Case Volume Growth", out, flags=re.I)
    out = re.sub(r"^(.+?)\s*-\s*unit case volume growth.*$", r"\1 Unit Case Volume Growth",
                 out, flags=re.I)
    out = re.sub(r"^\s*(?:y/y|yoy)\s*-\s*(?:total\s+)?(.+)$", r"\1 Growth", out, flags=re.I)
    out = re.sub(r"^(\w+) - consolidated revenue by geography$", r"\1 Revenue", out, flags=re.I)
    out = re.sub(r"^(.+?) - average membership$", r"\1 Membership (average)", out, flags=re.I)
    out = re.sub(r"^(.+?) - (?:period[- ]end|eop) membership$", r"\1 Membership (period end)", out, flags=re.I)
    out = re.sub(r"^(.+?) - membership$", r"\1 Membership (period end)", out, flags=re.I)
    out = re.sub(r"^(us|u\.s\.|international|europe|asia pacific|latin america|cemea|canada)"
                 r" - payment volumes?$", r"\1 Payment Volume", out, flags=re.I)
    out = re.sub(r"^total (energy & transportation|construction industries|resource industries)"
                 r"\s*-\s*machinery & power systems sales$", r"\1 Sales", out, flags=re.I)
    out = re.sub(r"^\s*(?:q/q|qoq)\s*-\s*(.+)$", r"\1, quarter on quarter", out,
                 flags=re.I)
    out = re.sub(r"\s*-\s*consolidated\s*$", "", out, flags=re.I)
    out = re.sub(r"\s*\(\s*%\s*\)\s*$", "", out)
    out = re.sub(r"^\s*%\s+", "", out)
    out = re.sub(r"\s*\((aycr|aylr|cycr|cylr|dso|dio|dpo|roe|roa|roic|nim|nii|"
                 r"pif|arpu|npw|npe|dcf|affo|affo\s*/\s*dcf)\)",
                 "", out, flags=re.I)
    out = re.sub(r"(\w)-\s+", r"\1 - ", out)
    out = re.sub(r"^(total|net|gross|average)\s+(\w+)\s+-\s+(\w+)$", r"\1 \2 \3", out,
                 flags=re.I)
    out = re.sub(r"^margin\s*-\s*(.+)$", r"\1 Margin", out, flags=re.I)
    out = re.sub(r"\s+([,.])", r"\1", out)
    return re.sub(r"\s{2,}", " ", out).strip(" -")


_Q = r"(?:\s*\([^)]*\))?"
SPINE_PAT = re.compile(
    r"^total revenue" + _Q + r"$|^revenue" + _Q + r"$|^net sales" + _Q + r"$|"
    r"gross margin|^gm[ -]|^gm\s*\(|"
    r"margin\s*-\s*(\w+\s+)?(ebit|ebitda|net income|gross)|"
    r"ebit margin|ebitda margin|operating margin|net margin|"
    r"^(total\s*-\s*)?(adjusted|modified|core|underlying)?\s*ebitda(x)?" + _Q + r"$|"
    r"^(adjusted|modified)?\s*ebitda(x)?\s*-\s*(total|consolidated)$|"
    r"^eps\b|^adjusted eps|^diluted eps|^basic eps|earnings per share|"
    r"^ocf" + _Q + r"$|^fcf" + _Q + r"$|operating cash flow|free cash flow|"
    r"net interest income|net interest margin|combined ratio|"
    r"^net premiums? (written|earned)" + _Q + r"$|"
    r"rate base|"
    r"average daily production|^total production" + _Q + r"$|"
    r"revenue per boe|wellhead revenue per boe|"
    r"distributable cash flow|^total throughput" + _Q + r"$|"
    r"coverage ratio|distribution coverage|"
    r"operating ratio|freight revenue|revenue per carload|"
    r"medical (loss|care) ratio|\bmlr\b|^total membership" + _Q + r"$|"
    r"core ffo\s*/\s*share|^ffo\s*/\s*share|^core ffo" + _Q + r"$|^ffo" + _Q + r"$|"
    r"same store noi|^%?\s*average occupancy|^occupancy rate|"
    r"^%?\s*organic\s*-\s*total revenue|organic revenue|"
    r"^(global|total) volume\s*-\s*unit case volume|^%?\s*volume\s*-\s*total revenue",
    re.I)
SECTOR_KPI_PAT = re.compile(
    r"\barr\b|remaining performance|\brpo\b|billings|bookings|backlog|"
    r"same store|comparable sales|\bsss\b|system[- ]wide sales|subscriber|\barpu\b|retention|churn|"
    r"\bloans?\b|\bdeposits?\b|provision|charge-?off|\bcet\s?1\b|"
    r"combined ratio|loss ratio|premium|production|realized price|"
    r"units|average selling price|\basp\b|traffic|ticket|"
    r"rent change|leases? commenced|development starts|\baffo\b|"
    r"unit case volume|price\s*/?\s*mix|concentrate", re.I)


SEGMENT_REV_PAT = re.compile(r"revenue|net sales", re.I)


def tier_basis(name, tags, revenue_share=None, category=None, segment_scoped=False):
    t = (tags or "").lower()
    if not segment_scoped and (SPINE_PAT.search(name or "")
                               or "top_kpi" in t or "earnings_flash_kpi" in t):
        return "spine_or_top_tag"
    if (revenue_share is not None and (category or "").upper().startswith("SD")
            and SEGMENT_REV_PAT.search(name or "")):
        if revenue_share >= 0.25:
            return "segment_size"
        if revenue_share >= 0.10:
            return "segment_size"
    if "company_kpi" in t or "driver_kpi" in t or SECTOR_KPI_PAT.search(name or ""):
        return "sector_or_driver"
    return "default"


def tier_of(name, tags, revenue_share=None, category=None, segment_scoped=False):
    t = (tags or "").lower()
    if not segment_scoped and (SPINE_PAT.search(name or "")
                               or "top_kpi" in t or "earnings_flash_kpi" in t):
        return 1
    _ = segment_scoped
    if (revenue_share is not None and (category or "").upper().startswith("SD")
            and SEGMENT_REV_PAT.search(name or "")):
        if revenue_share >= 0.25:
            return 1
        if revenue_share >= 0.10:
            return 2
    if "company_kpi" in t or "driver_kpi" in t or SECTOR_KPI_PAT.search(name or ""):
        return 2
    return 3


POLARITY_OVERRIDES = [
    (-1, r"\brevenue\s+tax(es)?\b|\brevenue\s+deductions?\b|\brevenue\s+reserves?\b|"
         r"\bsales\s+returns?\s+reserves?\b",
     "a tax, deduction or reserve charged against revenue is a cost, whatever word it borrows"),
    (0, r"^\s*(less:\s*)?(capex|capital expenditures?)\b",
     "capital expenditure is investment as often as it is overspend; its direction "
     "carries no read of its own"),
    (0, r"income\s*/\s*expense|expense\s*/\s*income|income \(expense\)",
     "a net line that can be income or expense: which way is better depends on "
     "which side of zero it sits and why it moved"),
    (0, r"payout ratio",
     "a payout ratio has no better direction of its own: a higher payout is more "
     "cash returned and less retained"),
    (0, r"^accounts payable|\baccounts payable\b(?!.*days)",
     "payables have no better direction of their own: faster growth is supplier "
     "financing, slower growth can be tighter terms or better discipline"),
    (0, r"cap rate|capitalization rate",
     "a cap rate has no better direction of its own: a higher cap rate is a "
     "cheaper asset and a weaker price"),
    (0, r"days claims payable|\bdcp\b|claims payable",
     "a reserve-adequacy measure, not a payment-terms measure: a fall can mean "
     "faster settlement or reserve release, and the series cannot separate them"),
    (0, r"membership|members\b|covered lives|lives covered|enrol(l)?ment",
     "a shrinking book cuts the revenue base and can also improve the loss ratio "
     "if the business that left was underwritten below cost; read it against the "
     "loss ratio, not on its own"),
    (0, r"(?!.*\b(price|cost|rate)\b)"
        r"(fuel gallons? (consumed|used)|gallons consumed|fuel consumption|"
        r"energy consum|diesel consum)",
     "consumption tracks activity, so it carries no implication until it is read "
     "against a traffic-output line"),
]

POLARITY_RULES = [
    (-1, r"^\s*(labou?r|occupancy|other (restaurant )?operating (costs?|expenses?)|"
         r"food,? (and |& )?(beverage|paper)( (and|&) packaging)?)\b", ("restaurants",)),
    (-1, r"purchased power|fuel (and|&) purchased power|fuel for (electric )?generation",
     ("utilities",)),
    (-1, r"stock[- ]?(based )?comp|share[- ]based comp|equity[- ]based comp|\bsbc\b"),
    (1, r"inventory turn(over|s)?\b"),
    (1, r"\bauv\b|average unit volumes?|average (restaurant|store|unit) (sales|volumes?)"),
    (1, r"return on (average )?(tangible )?(common )?(equity|assets|invested capital)|"
        r"non[- ]?interest income|\bfee income\b|\bppnr\b|"
        r"pre-?provision (net )?(revenue|income|profit|earnings)|"
        r"(opened|openings?)\b.*\b(restaurants?|stores?|units?|locations?)|"
        r"new (restaurants?|stores?|units?) opened"),
    (0, r"solvency ratio|capital adequacy"),
    (-1, r"occupancy (costs?|expenses?)"),
    (-1, r"\bvacancy\b"),
    (1, r"^%?\s*(price|volume|organic)\s*-\s|price realization|^%?\s*organic\b"),
    (-1, r"cancellation"),
    (1, r"selling price|\basp\b|\bsss\b|(?<!stock )\bcomps?\b(?!\s*(expense|cost|award))|net orders|homes? closed|\bclosings\b|"
        r"system[- ]wide sales|installed base|\bprocedures\b|placements|systems? placed|"
        r"shop visits|engines? deliver|\bdeliveries\b|gross orders|\borders\b|"
        r"\bmw sold\b|modules? sold|volume sold|\bmw produced\b|net bookings"),
    (-1, r"sales\s*(&|and)\s*marketing|\bselling\b(?!\s*price)|"
         r"inventor|receivab|days sales|\bdso\b|\bdio\b|\bdpo\b|churn|"
         r"accounts payable|days payable|"
         r"medical care ratio|care ratio|benefit ratio|\bmcr\b|"
         r"medical cost ratio|claims ratio|"
         r"attrition|cost of|\bcogs\b|expense|opex|\bsg&a\b|\bs&m\b|"
         r"\bdebt\b|leverage|net loss|impair|restructur|provision|charge-?off|"
         r"delinquen|non-?accrual|non-?performing|combined ratio|loss ratio|"
         r"unit cost|tax rate|\betr\b|share count|dilut(ed|ion) shares|"
         r"cost\s*/|cost per|costs?\s*per\b|(cost|expense|dd&a)[^,;]*per boe|"
         r"costs?\s*\(usd|"
         r"\b(operating|cash|total|unit|lifting|production|well)\s+costs?\b|"
         r"\bdd&a\b|catastrophe loss|reserve development|losses incurred|"
         r"\bshrink\b|markdown|efficiency ratio|expense ratio|"
         r"operating ratio|combined operating ratio|"
         r"\bcasm\b|cost per available seat|"
         r"price\s*/\s*fuel|fuel price|price per gallon|"
         r"disconnect|involuntary deactivation|"
         r"aircraft fuel|aircraft rent|aircraft maintenance|landing fee|"
         r"passenger service|salaries|\bwages\b|employee compensation|"
         r"cost[- ]to[- ]income|\bnco\b|non-?accrual"),
    (1, r"revenue|net sales|\bsales\b|gross profit|gross margin|\bgm\b|ebit|ebitda|operating income|"
        r"net income|\beps\b|earnings per share|margin|cash flow|\bfcf\b|\bocf\b|"
        r"backlog|bookings|billings|remaining performance|\brpo\b|\barr\b|"
        r"subscriber|customers|"
        r"(customer|client|subscriber|member|tenant|logo|revenue|dollar|gross|net)\s+"
        r"retention|retention rate|renewal|same store|comparable sales|"
        r"traffic|ticket|net interest income|net interest margin|deposits|"
        r"\b[pt]?rasm\b|revenue per available seat|load factor|"
        r"passenger mile yield|yield\s*/\s*rpm|\brpms?\b|"
        r"\baum\b|assets under management|net flows|net new assets|"
        r"rate base|regulatory asset base|\brab\b|"
        r"base fee|advisory fee|performance fee|"
        r"payment volume|processed transactions|cross border volume|"
        r"\bffo\b|\baffo\b|funds from operations|rent change|"
        r"\bnoi\b|net operating income|rental income|strategic capital income|"
        r"leases? commenced|new leases|"
        r"unit case volume|organic|price\s*/?\s*mix|"
        r"\barpu\b|\barpa\b|subs\b|net adds|gross adds|"
        r"broadband customers|postpaid|prepaid\b|"
        r"book value per share|premium|interest coverage|distribution coverage|"
        r"coverage ratio|\broic\b|\broe\b"),
    (1, r"occupan", ("reit",)),
    (0, r"\br&d\b|research and development|capex|capital expenditure|production|"
        r"volumes|barrels|\bboe\b|throughput|"
        r"headcount|employees|total assets|securities|loans|investment|"
        r"\brwa\b|risk-?weighted|\bcet\s?1\b|tier\s?1|capital|cash and equivalent|"
        r"deferred revenue|dividend|buyback|repurchase|lease"),
]


SHARE_OF_PAT = re.compile(r"^\s*%\s*(revenue|total revenue|sales|opex|"
                          r"operating expenses?)\s*[-–]\s*", re.I)
SHARE_GOOD_SUBJECT = re.compile(
    r"\bfcf\b|free cash flow|\bocf\b|operating cash flow|gross profit|"
    r"\bebitda?\b|net income|margin|profit\b", re.I)
SHARE_NEUTRAL_SUBJECT = re.compile(
    r"capex|capital expenditure|\br&d\b|research and development|"
    r"revenues?\s*$|\bsales\s*$|"
    r"marketing|advertising|brand investment|digital investment", re.I)


ANY_TEMPLATE = object()


def _load_polarity_registry():
    path = os.environ.get("INFLECTION_POLARITY_REGISTRY") or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "assets", "polarity_registry.json")
    try:
        with open(path, encoding="utf-8") as fh:
            reg = json.load(fh)
    except (OSError, ValueError):
        return {}
    out = {}
    for code, e in (reg.get("codes") or {}).items():
        if isinstance(e, dict) and e.get("polarity") in (-1, 0, 1):
            out[code] = int(e["polarity"])
    return out


POLARITY_REGISTRY = _load_polarity_registry()


def _rule_applies(rule, template):
    owner = rule[2] if len(rule) > 2 else None
    return (owner is None or template is ANY_TEMPLATE
            or (template is not None and template in owner))


def resolve_polarity(name, ltcode=None, template=ANY_TEMPLATE, facts=None):
    if facts:
        v = _shape_polarity_before(name, facts)
        if v is not None:
            return v
        v, st, src = resolve_polarity(name, ltcode, template)
        if v == 0:
            after = _shape_polarity_after(name, facts)
            if after is not None:
                return after
        return v, st, src
    n = subject_name(name).lower()
    _fm = re.match(r"^\(\s*(.+?)\s+-\s+(.+?)\s*\)\s*/\s*(total\s+|net\s+)?(revenue|sales)\b",
                   (name or "").strip(), re.I)
    if _fm:
        v, st, src = resolve_polarity(_fm.group(1), None, template)
        return v, st, "formula:" + src
    m = SHARE_OF_PAT.match(n)
    if m:
        subject = n[m.end():].strip()
        if SHARE_NEUTRAL_SUBJECT.search(subject):
            return 0, "neutral", "share_of_revenue:neutral_subject"
        if SHARE_GOOD_SUBJECT.search(subject):
            return 1, "declared", "share_of_revenue:profit_subject"
        return -1, "declared", "share_of_revenue:burden"
    for pol, pat, _why in POLARITY_OVERRIDES:
        if re.search(pat, n):
            return pol, ("neutral" if pol == 0 else "declared"), "override"
    if ltcode and ltcode in POLARITY_REGISTRY:
        v = POLARITY_REGISTRY[ltcode]
        return v, ("neutral" if v == 0 else "declared"), "registry:" + ltcode
    for rule in POLARITY_RULES:
        if not _rule_applies(rule, template):
            continue
        if re.search(rule[1], n):
            return rule[0], ("neutral" if rule[0] == 0 else "declared"), "name_rule"
    return 0, "undeclared", "none"


COUNT_NOUN_PAT = re.compile(r"\b(restaurants?|stores?|locations?|branch(es)?|outlets?|"
                            r"units?|sites?|employees|headcount|clinics?|hotels?|"
                            r"properties|doors|warehouses?|clubs?)\b", re.I)
MONEY_WORD_PAT = re.compile(r"sales|revenue|cost|expense|income|profit|margin|fees?\b|"
                            r"\brent\b|deposit|loan|cash|capex|spend|price|value|"
                            r"volume|aum|asset|liabilit|debt|ebit|earnings|eps|"
                            r"payable|receivab|inventor|wage|tax|interest|dividend", re.I)


def is_count_name(name):
    n = name or ""
    return bool(COUNT_NOUN_PAT.search(n)) and not MONEY_WORD_PAT.search(n)


SEGMENT_NOT_REVENUE_PAT = re.compile(
    r"cost|expense|capex|capital|asset|liabilit|debt|"
    r"inventor|receivab|payable|depreciat|amorti|tax|"
    r"interest|impair|charge|loss|headcount|employee|"
    r"labou?r|wage|payroll|salar|fuel|purchased power|"
    r"\brent\b|occupan|maintenan|repair|marketing|"
    r"advertis|claims?\b|refund|returns? and allowance|"
    r"discounts?\b|write[- ]?offs?|settlement|freight|"
    r"benefits? paid|policyholder benefits|professional fees|spend\b|"
    r"development\b", re.I)


def _shape_polarity_before(name, facts):
    kind, cat = facts.get("kind"), (facts.get("category") or "").upper()
    if (kind == "ratio" and cat.startswith("SD")
            and re.match(r"^\s*%\s*revenue\s*-\s*", name or "", re.I)
            and not re.search(r"cost|expense|capex|opex|sg&a|r&d|compensation|"
                              r"depreciat|amortiz|interest|tax", name or "", re.I)):
        return 0, "neutral", "shape:segment_mix_share"
    if (kind == "level" and facts.get("currency")
            and re.match(r"^\s*occupancy\b", name or "", re.I)):
        return -1, "declared", "shape:occupancy_cost_level"
    return None


def _shape_polarity_after(name, facts):
    if ((facts.get("category") or "").upper().startswith("SD")
            and facts.get("kind") == "level" and facts.get("currency")
            and not facts.get("negative_convention")
            and not is_count_name(name)
            and not SEGMENT_NOT_REVENUE_PAT.search(name or "")):
        return 1, "inferred", "shape:segment_product_revenue"
    return None


def polarity_of(name, ltcode=None, template=ANY_TEMPLATE):
    return resolve_polarity(name, ltcode, template)[0]


def lower_is_better(name, ltcode=None, template=ANY_TEMPLATE):
    return polarity_of(name, ltcode, template) == -1


def polarity_note(name):
    n = subject_name(name).lower()
    for _pol, pat, why in POLARITY_OVERRIDES:
        if why and re.search(pat, n):
            return why
    return None



DERIVED_SUFFIXES = ("-Y", "-Q", "-BY", "-M1", "-QY", "-YQ")
DERIVED_NAME_PAT = re.compile(
    r"^\s*(y/y|q/q|yoy|qoq)\s*-|^\s*change in bps|^\s*incremental margin|"
    r"\(y/y\)|\(q/q\)|^\s*%\s*fx\b", re.I)

NOISE_NAME_PAT = re.compile(
    r"stock split multiplier|total liabilities & shareholders equity|"
    r"effect of foreign exchange|accumulated other comprehensive|"
    r"preferred stock|common stock - bs|restricted cash|"
    r"proceeds from (issuance|maturities)|purchases of marketable securities|"
    r"average share price|net change in cash", re.I)

SUBPERIOD_PAT = re.compile(r"\b(month|week)\s*-?\s*\d+\b|"
                           r"-\s*(month|week)\s*-?\s*\d+\b|"
                           r"\b(four|five)\s+weeks?\b|"
                           r"\bfrom the previous (month|week)\b|"
                           r"\b(month|week)[\s-]*(to|over)[\s-]*(month|week)\b|"
                           r"\b(mom|wow)\b", re.I)

ADJ_TOKEN_PAT = re.compile(
    r"\s*\((ex-1x items?|ex 1x items?|non-?gaap|adjusted|fte|"
    r"fully taxable equivalent|managed basis)\)", re.I)

BASIS_TOKEN_PAT = re.compile(
    r"\baccident year\s*|\bcalendar year\s*|"
    r"\s*\(?(aycr|aylr|cycr|cylr)\)?|"
    r"\s*-?\s*ex[- ]cats?\b|\s*-?\s*excluding catastrophes?\b|"
    r"\s*-?\s*ex[- ]catastrophes?\b", re.I)

ATTRIB_PAT = re.compile(r"^\s*%\s*(fx|cc|constant currency)\b|"
                        r"\bcontribution\b|\bconstant currency\b|"
                        r"\bbridge\b|\bwalk\s*$|\breconciliation\b|"
                        r"[-–]\s*rec\s*$|\brec\s*$", re.I)


DECOMPOSITION_PAT = re.compile(
    r"\bbridge\s*$|\s-\s*bridge\b|\breconciliation\b|"
    r"[-\u2013]\s*rec\s*$|\(non-?gaap\)\s*rec\s*$|\bwalk\s*$", re.I)


def concept_key(name: str) -> str:
    return re.sub(r"\s+", " ", ADJ_TOKEN_PAT.sub("", name or "")).strip().lower()


def screen_universe(items):
    kept, dropped = [], []

    def drop(it, reason):
        dropped.append({"ltcode": it.get("ltcode"), "name": it.get("name"), "reason": reason})

    all_codes = {(it.get("ltcode") or "").strip() for it in items}

    def base_code_exists(code):
        for suf in DERIVED_SUFFIXES:
            if code.endswith(suf):
                return code[: -len(suf)] in all_codes
        return False

    staged = []
    for it in items:
        code = (it.get("ltcode") or "").strip()
        name = (it.get("name") or it.get("lineitem") or "").strip()
        if not code:
            continue
        if code.endswith("-Y") and not base_code_exists(code) and not ATTRIB_PAT.search(name):
            staged.append({"ltcode": code, "name": name,
                           "tags": (it.get("tags") or "") + "|pre_growth"})
        elif code.endswith(DERIVED_SUFFIXES) or DERIVED_NAME_PAT.search(name):
            drop(it, "already a growth or delta series")
        elif NOISE_NAME_PAT.search(name):
            drop(it, "accounting identity or market artefact")
        elif DECOMPOSITION_PAT.search(name):
            drop(it, "reconciliation or bridge row, not a metric")
        elif SUBPERIOD_PAT.search(name):
            drop(it, "monthly or weekly slice of a series already read quarterly")
        else:
            staged.append({"ltcode": code, "name": name, "tags": it.get("tags") or ""})

    groups = {}
    for it in staged:
        groups.setdefault(concept_key(it["name"]), []).append(it)
    for _, g in sorted(groups.items()):
        if len(g) == 1:
            kept.append(g[0])
            continue
        adj = [x for x in g if "adjusted" in (x["tags"] or "").lower()]
        winner = sorted(adj or g, key=lambda x: x["ltcode"])[0]
        kept.append(winner)
        for x in g:
            if x is not winner:
                drop(x, "reported twin of an adjusted line already scanned")

    kept.sort(key=lambda x: x["ltcode"])
    return kept, dropped

WORKING_CAPITAL_PAT = re.compile(r"\b(inventor|receivab)\w*", re.I)
NO_WORKING_CAPITAL_SECTORS = {"airlines", "asset_mgmt", "banks", "insurance",
                              "utilities", "midstream", "healthcare"}
NOT_WORKING_CAPITAL_PAT = re.compile(
    r"derivativ|trading|securities borrowed|reverse repo|fed funds", re.I)
FINANCIALS_PAT = re.compile(r"bank|insur|capital market|asset manage|broker|"
                            r"financial services|thrift|reit", re.I)



SCALE_MULT = {"K": 1e3, "TH": 1e3, "M": 1e6, "MM": 1e6, "MN": 1e6,
              "B": 1e9, "BN": 1e9, "T": 1e12, "TN": 1e12, "TR": 1e12,
              "X": 1.0}
CURRENCY_PREFIX = r"(?P<cur>[A-Za-z]{1,3}\$|[$£€¥₹₩₽¢]|[A-Za-z]{1,8})?"
VALUE_PAT = re.compile(
    r"^\s*(?P<sign>-|\()?\s*" + CURRENCY_PREFIX + r"\s*(?P<paren>\()?\s*"
    r"(?P<num>-?[\d,]*\.?\d+(?:[eE][+-]?\d+)?)\s*(?P<close>\))?\s*"
    r"(?P<scale>MM|BN|TN|TR|TH|MN|[KMBTX](?![A-Za-z]{2}))?\s*"
    r"(?P<pct>%|pp\b|bps\b|bp\b)?\s*(?P<iso>[A-Z]{3})?\s*\)?\s*$", re.I)
NOT_MEANINGFUL_PAT = re.compile(r"^\s*(n\.?\s*m\.?|n\.?\s*a\.?|nmf|not meaningful)\s*$", re.I)
MINOR_UNIT_PREFIX = re.compile(r"^(?i:(us)?cents?)$|^[A-Z]{2}[a-z]$")


def parse_value(x):
    if x is None:
        return None, False
    if isinstance(x, (int, float)):
        if isinstance(x, float) and math.isnan(x):
            return None, False
        return float(x), False
    s = str(x).strip()
    if s.lower() in ("", "-", "--", "n/a", "na", "none", "null", "nm") or NOT_MEANINGFUL_PAT.match(s):
        return None, False
    m = VALUE_PAT.match(s)
    if not m:
        return None, False
    if bool(m.group("paren")) != bool(m.group("close")) and not (m.group("sign") == "(" and m.group("close")):
        return None, False
    try:
        val = float(m.group("num").replace(",", ""))
    except ValueError:
        return None, False
    if m.group("scale"):
        val *= SCALE_MULT[m.group("scale").upper()]
    _p = (m.group("pct") or "").lower()
    if _p in ("bps", "bp"):
        val /= 100.0
    if m.group("paren"):
        val = -abs(val)
    if _is_pence(m.group("cur")):
        val /= 100.0
    if m.group("sign") or s.startswith("(") and s.endswith(")"):
        val = -abs(val)
    return val, bool(m.group("pct"))


def _is_pence(cur):
    return bool(cur) and (cur == "GBp" or cur.lower() in ("gbpence", "gbx"))


CURRENCY_SYMBOL = {"$": "$", "£": "£", "€": "\u20ac", "¥": "¥", "₹": "₹",
                   "₩": "₩", "₽": "₽"}


def ticker_slug(t):
    return re.sub(r"[^A-Za-z0-9._-]+", "-", str(t or "").strip()).strip("-") or "TICKER"


def parse_currency(x):
    if x is None or isinstance(x, (int, float)):
        return None
    m = VALUE_PAT.match(str(x).strip())
    if not m:
        return None
    if not m.group("cur") and m.group("iso") and m.group("iso").isupper():
        return m.group("iso")
    if not m.group("cur"):
        return None
    cur = m.group("cur").strip()
    if cur in CURRENCY_SYMBOL:
        return CURRENCY_SYMBOL[cur]
    if _is_pence(cur) or cur == "GBP":
        return "GBP"
    if re.fullmatch(r"[A-Za-z]{1,3}\$", cur):
        return cur.upper()
    if MINOR_UNIT_PREFIX.match(cur):
        return None
    return cur.upper() if len(cur) == 3 and cur.isalpha() else None


def is_multiple(x):
    if not isinstance(x, str):
        return False
    return bool(re.search(r"\d\s*[xX]\s*\)?\s*$", x.strip()))


def _quartile(vals, q):
    s = sorted(vals)
    if not s:
        return None
    if len(s) == 1:
        return s[0]
    pos = q * (len(s) - 1)
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return s[lo]
    return s[lo] + (s[hi] - s[lo]) * (pos - lo)


def _bin_z(z):
    if z is None:
        return "na"
    for lo, hi, name in Z_BINS:
        if lo <= z < hi:
            return name
    return "vpos" if z >= 0 else "vneg"


def _clamp_display(z):
    if z is None:
        return None
    return max(-Z_DISPLAY_CAP, min(Z_DISPLAY_CAP, z))


HALF_YEAR_PAT = re.compile(r"^([12]H\d{2}|H[12]\s?\d{2,4}|FY?\d{2,4}|\d{4})$", re.I)
HALF_YEAR_MESSAGE = ("%r is a half-year or annual period. This skill scores quarter-by-quarter "
                     "changes in growth and does not run on companies that report only "
                     "half-yearly or annually; tell the user the company is outside its scope "
                     "(an annual or half-year trend view is a different analysis)")


def quarter_sequence(latest_quarter: str, n: int):
    m = re.fullmatch(r"([1-4])Q(\d{2})", clean_period_label(latest_quarter).upper())
    if not m:
        if HALF_YEAR_PAT.match(clean_period_label(latest_quarter)):
            raise SystemExit(HALF_YEAR_MESSAGE % latest_quarter)
        raise SystemExit(f"latest-quarter must look like 1Q27, got {latest_quarter!r}")
    q, yy = int(m.group(1)), int(m.group(2))
    out = []
    for _ in range(n):
        out.append(f"{q}Q{yy:02d}")
        q -= 1
        if q == 0:
            q = 4
            yy = (yy - 1) % 100
    return list(reversed(out))



def _extract_discovered(blob):
    rows, seen = [], set()
    for d in _walk_dicts(blob):
        code = d.get("ltcode")
        if not code or not (d.get("name") or d.get("lineitem")) or d.get("QUARTERENDDATE"):
            continue
        if code in seen:
            continue
        seen.add(code)
        rows.append(d if d.get("name") else dict(d, name=d.get("lineitem")))
    return rows


def discovered_rows(paths):
    if not paths:
        return []
    if isinstance(paths, str):
        paths = [paths]
    rows, seen = [], set()
    for _f, blob in load_responses(list(paths)):
        for r in _extract_discovered(blob):
            if r["ltcode"] not in seen:
                seen.add(r["ltcode"])
                rows.append(r)
    return rows


def annual_periods(latest_quarter, n=3):
    m = re.fullmatch(r"([1-4])Q(\d{2})", str(latest_quarter or "").strip().upper())
    if not m:
        return []
    q, yy = int(m.group(1)), int(m.group(2))
    last = 2000 + (yy if q == 4 else yy - 1)
    return [str(y) for y in range(last - n + 1, last + 1)]


def _planned_batches(discovered, sweep, latest_quarter):
    periods = quarter_sequence(latest_quarter, LOOKBACK_QUARTERS)
    per_call = max(1, 100 // len(periods))
    rows = list(discovered_rows(discovered))
    tagged = {(r.get("ltcode") or "").strip() for r in rows}
    if sweep:
        swept = discovered_rows(sweep)
        fresh = [r for r in swept if (r.get("ltcode") or "").strip() not in tagged]
        seen = set()
        for r in sorted(fresh, key=lambda x: (x.get("ltcode") or "")):
            c = (r.get("ltcode") or "").strip()
            if c and c not in seen:
                seen.add(c)
                rows.append(r)
    kept, _dropped = screen_universe(rows) if rows else ([], [])
    codes = [k["ltcode"] for k in kept]
    codes += [d["ltcode"] for d in _dropped if d.get("reason", "").startswith("reported twin")
              and d["ltcode"] not in codes]
    if len(codes) < 10:
        for c in CORE_LTCODES:
            if c not in codes:
                codes.append(c)
    return [b.split(",") for b in _stable_batches(codes, tagged, per_call)]


def _stable_batches(codes, tagged, per_call):
    tag = [c for c in codes if c in tagged]
    rest = [c for c in codes if c not in tagged]
    return ([",".join(tag[i:i + per_call]) for i in range(0, len(tag), per_call)]
            + [",".join(rest[i:i + per_call]) for i in range(0, len(rest), per_call)])


def _count_empty_success(blob):
    n = 0
    if isinstance(blob, list):
        return sum(_count_empty_success(x) for x in blob)
    if not isinstance(blob, dict):
        return 0
    if str(blob.get("status")) == "success" and isinstance(blob.get("data"), list) \
            and not blob["data"] and not blob.get("notes"):
        return 1
    for v in blob.values():
        if isinstance(v, (dict, list)):
            n += _count_empty_success(v)
    return n


def _provider_says_untagged(paths):
    try:
        for _f, blob in load_responses(list(paths or [])):
            for d in _walk_dicts(blob):
                for n in (d.get("notes") or []) if isinstance(d.get("notes"), list) else []:
                    if re.search(r"no kpi-tagged line items", str(n), re.I):
                        return True
    except IngestError:
        return False
    return False


ANNUAL_CODES_PER_CALL = 30


def _annual_calls(codes, ticker_id, latest_quarter):
    fy = annual_periods(latest_quarter)
    per = max(1, min(ANNUAL_CODES_PER_CALL, 100 // max(1, len(fy))))
    return [{"tool_name": "get_actuals",
             "tool_arguments": {"ticker_id": str(ticker_id or "<id>"),
                                "ltcode": ",".join(codes[i:i + per]),
                                "fiscal_period": ",".join(fy), "limit": "100"}}
            for i in range(0, len(codes), per)]


def cmd_plan(args):
    periods = quarter_sequence(args.latest_quarter, LOOKBACK_QUARTERS)
    per_call = max(1, 100 // len(periods))

    try:
        rows = list(discovered_rows(args.discovered))
        swept = discovered_rows(args.sweep) if args.sweep else []
    except IngestError as e:
        _fail("plan", str(e))
    _untagged = _provider_says_untagged(args.discovered)
    if len(rows) < 10 and _untagged and not getattr(args, "allow_spine", False):
        _sw_codes = {(r.get("ltcode") or "").strip() for r in swept} - {""}
        if len(_sw_codes) < 10:
            print(json.dumps({
                "status": "needs_sweep", "stage": "plan",
                "message": "the provider tags no KPIs for this company, so discovery is empty "
                           "by design. Run every keyword bank below with search_lineitem (no "
                           "filter), file the results under sweep/, and re-run plan; the sweep "
                           "is this company's universe",
                "keyword_banks": _keyword_plan(args.subindustry, args.ticker_id)}, indent=2))
            raise SystemExit(6)
    if len(rows) < 10 and not _untagged and not getattr(args, "allow_spine", False):
        _fail("plan", "discovery returned %d line items (fewer than 10). Re-run "
                      "search_lineitem(ticker_id, queries='all') with no filter and save the "
                      "whole response; --allow-spine plans the six spine codes on purpose"
                      % len(rows))
    for _sf in (args.sweep or []):
        if os.path.getsize(_sf) > 2 and not discovered_rows([_sf]):
            _fail("plan", "sweep file %s is not empty but yielded 0 line items; save "
                          "the search_lineitem responses as returned" % _sf)
    tagged_codes = {(r.get("ltcode") or "").strip() for r in rows}
    sweep_new = 0
    if args.sweep:
        fresh = [r for r in swept
                 if (r.get("ltcode") or "").strip() not in tagged_codes]
        seen = set()
        for r in sorted(fresh, key=lambda x: (x.get("ltcode") or "")):
            c = (r.get("ltcode") or "").strip()
            if c and c not in seen:
                seen.add(c)
                rows.append(r)
        sweep_new = len(seen)
    kept, dropped = screen_universe(rows) if rows else ([], [])

    codes = [k["ltcode"] for k in kept]
    _fallback = [d["ltcode"] for d in dropped if d.get("reason", "").startswith("reported twin")]
    codes += [c for c in _fallback if c not in codes]
    if len(codes) < 10:
        for c in CORE_LTCODES:
            if c not in codes:
                codes.append(c)
    batches = _stable_batches(codes, tagged_codes, per_call)
    _cf = [k["ltcode"] for k in kept if str(k["ltcode"]).startswith("CF_")
           or re.search(r"\bocf\b|\bfcf\b|cash flow|capex|capital expend", k.get("name") or "", re.I)]
    _fy = annual_periods(args.latest_quarter)
    _per_a = max(1, min(ANNUAL_CODES_PER_CALL, 100 // max(1, len(_fy))))
    annual_batches = [",".join(_cf[i:i + _per_a]) for i in range(0, len(_cf), _per_a)]

    plan = {
        "ticker": args.ticker.upper(),
        "latest_quarter": args.latest_quarter.upper(),
        "periods": periods,
        "periods_csv": ",".join(periods),
        "lookback_quarters": LOOKBACK_QUARTERS,
        "codes_per_call": per_call,
        "codes_to_fetch": len(codes),
        "batches": batches,
        "annual_periods_csv": ",".join(_fy),
        "annual_batches": annual_batches,
        "calls_per_request_suggested": 5,
        "requests_needed": (len(batches) + len(annual_batches) + 4) // 5,
        "universe_basis": ("keyword sweep: the provider tags no KPIs for this company"
                           if _untagged and len(tagged_codes - {""}) == 0 else "KPI tags plus keyword sweep"),
        "twin_fallback_codes": _fallback,
        "screened_out": dropped,
        "screened_out_count": len(dropped),
        "codes_from_keyword_sweep": sweep_new,
        "keyword_banks": _keyword_plan(args.subindustry, args.ticker_id),
        "discovery": {
            "step": "search_lineitem",
            "arguments": {"ticker_id": args.ticker_id or "<id>", "queries": "all"},
            "note": (
                "queries='all' with NO filter returns every KPI-tagged line item for "
                "the ticker across every tier, including the financial spine. Save that "
                "response and pass it back as --discovered so the engine screens it: the "
                "dictionary ships pre-derived Y/Y, Q/Q and delta companions, and "
                "accelerating an already-differenced series produces a meaningless "
                "number that nonetheless ranks highly."
            ),
        },
        "constants": _constants(),
    }
    print(json.dumps(plan, indent=2) if args.json else _plan_text(plan))


SEARCH_KEYWORD_CAP = 15
ANNUAL_SUM_TOL = 0.015
ANNUAL_Q4_TOL = 0.005


def _keyword_plan(subindustry, ticker_id=None):
    banks = dict(KEYWORD_BANKS)
    for sb in sector_banks(subindustry):
        if sb in SECTOR_BANKS:
            banks[f"sector_{sb}"] = SECTOR_BANKS[sb]
    _split = {}
    for name, words in banks.items():
        words = list(words)
        if len(words) <= SEARCH_KEYWORD_CAP:
            _split[name] = words
        else:
            for k in range(0, len(words), SEARCH_KEYWORD_CAP):
                _split["%s_%d" % (name, k // SEARCH_KEYWORD_CAP + 1)] = words[k:k + SEARCH_KEYWORD_CAP]
    banks = _split
    return [{"bank": name,
             "tool": "search_lineitem",
             "arguments": {"ticker_id": ticker_id or "<id>", "queries": ", ".join(words),
                           "limit": "20"},
             "note": "no filter: a filter would re-impose the KPI tagging this "
                     "sweep exists to get past"}
            for name, words in banks.items()]


def _plan_text(p):
    lines = [
        f"Fetch plan for {p['ticker']} (latest reported {p['latest_quarter']})",
        f"  periods ({p['lookback_quarters']}): {p['periods_csv']}",
        f"  codes to fetch: {p['codes_to_fetch']}  "
        f"({p['codes_per_call']} per call, {len(p['batches'])} calls, "
        f"~{p['requests_needed']} requests)",
        f"  screened out of the universe: {p['screened_out_count']}",
        f"  net new codes found by keyword sweep: {p['codes_from_keyword_sweep']}",
    ]
    if p["batches"]:
        lines.append("  batches:")
        lines += [f"    {b}" for b in p["batches"]]
    else:
        lines.append("  no discovered universe supplied; run search_lineitem"
                     "(ticker_id, queries='all') and pass it as --discovered")
    return "\n".join(lines)


def _constants():
    return {
        "lookback_quarters": LOOKBACK_QUARTERS,
        "min_quarters_to_rank": MIN_Q_FOR_RANK,
        "min_quarters_full_confidence": MIN_Q_FOR_FULL_CONF,
        "min_accel_observations_for_z": MIN_ACCEL_OBS_FOR_Z,
        "base_floor_fraction_of_peak": BASE_FLOOR_FRAC,
        "material_abs_z": MATERIAL_Z,
        "large_move_multiple": LARGE_MOVE_MULT,
        "strong_abs_z": STRONG_Z,
        "heatmap_quarters": HEATMAP_QUARTERS,
        "rows_per_ranked_table": TOP_N,
        "deep_dive_rows": DEEP_DIVE_N,
    }



GROWTH_RATE_NAME_PAT = re.compile(
    r"^\s*%\s*(organic|cc|constant currency|volume|price(\s*/?\s*mix)?|mix|fx|currency|m&a)\s*-"
    r"|^\s*%\s*(traffic|guest (count|traffic)s?|transactions?|comparable transactions|"
    r"(average )?(ticket|check)|units?|unit volume|pricing|acquisitions?|"
    r"divestitures?|foreign exchange)\s*-\s*(total\s+|net\s+)?(revenues?|sales)\b"
    r"|\bvolume growth\b|\bgrowth\s*\(%\)|^\s*(y/y|yoy)\s*-"
    r"|same[- ]store (noi|sales)|comparable (store )?sales|organic (revenue )?growth"
    r"|^\s*%?\s*sss\b|\bcomps?\b\s*$|stack\s*-\s*sss"
    r"|\brent change\b"
    r"|\s-\s*growth(\s*\(ex-?fx\))?\s*$|base business growth"
    r"|^\s*(volume|price)\s*$|\bsales growth\s*$"
    r"|^\s*%?\s*(translation|fx|currency|acquisitions?|divestitures?|m&a)\s+revenue\s*$", re.I)


COPY_MIN_SIG_DIGITS = 4


def _sig_digits(x):
    try:
        t = ("%.12g" % abs(float(x))).split("e")[0].replace(".", "").lstrip("0").rstrip("0")
    except (TypeError, ValueError):
        return 0
    return len(t)


class Series:

    def __init__(self, meta):
        self.ltcode = meta.get("ltcode") or meta.get("LTCODE")
        self.name = (meta.get("lineitem") or meta.get("LINEITEM") or self.ltcode or "").strip()
        self.category = (meta.get("category") or meta.get("CATEGORY_CODE") or
                         (self.ltcode.split("_")[0] if self.ltcode else "")).upper()
        self.fmt = (meta.get("format") or meta.get("FORMAT_CODE") or "N").upper()[:1]
        self.scale = meta.get("scale") or meta.get("SCALE") or ""
        self.currency = meta.get("currency") or meta.get("CURRENCY") or ""
        self.tags = meta.get("tags") or ""
        self.points = {}
        self.pct_votes = 0
        self.currency_votes = 0
        self.multiple_votes = 0
        self.sources = {}
        self.uic = None
        self.pre_growth = "pre_growth" in (meta.get("tags") or "")

        self.kind = None
        self.unit = None
        self.values = []
        self.periods = []
        self.growth = []
        self.accel = []
        self.flags = []
        self.negative_convention = False

    def _bare_place_rate(self):
        nm = (self.name or "").strip()
        if not str(getattr(self, "ltcode", "") or "").startswith("SD_"):
            return False
        if not re.fullmatch(r"[A-Z][A-Za-z.&' ]{1,30}", nm) or len(nm.split()) > 3 \
                or BARE_PLACE_METRIC_PAT.search(nm):
            return False
        vals = [v for v in (self.values or []) if v is not None]
        return len(vals) >= 5 and any(v < 0 for v in vals)

    def classify(self):
        if (not self.pre_growth and self.pct_votes > len(self.values) / 2
                and (GROWTH_RATE_NAME_PAT.search(self.name or "") or self._bare_place_rate())
                and not re.search(r"\b(margin|ratio|share of|as a? ?% of|yield)\b",
                                  self.name or "", re.I)):
            self.pre_growth = True
        if self.pre_growth:
            self.kind = "pre_growth"
            self.unit = "pct"
            self.pct_is_fraction = False
            return
        value_says_ratio = self.pct_votes > len(self.values) / 2
        value_says_level = (self.pct_votes == 0
                            and (getattr(self, "currency_votes", 0) > 0
                                 or getattr(self, "multiple_votes", 0) > 0))
        name_says_ratio = (
            (re.search(r"\b(margin|ratio|as a %|percent|share of)\b",
                       self.name, re.I)
             or (re.search(r"\byield\b", self.name, re.I)
                 and not re.search(r"yield\s*/|per (passenger )?mile|"
                                   r"passenger mile yield|\(cents?\)|/\s*rpm",
                                   self.name, re.I)))
            or re.search(r"\b(growth|utilization|utilisation|retention|churn|"
                         r"capacity|payout|attrition|tax|interest|coupon|"
                         r"conversion|win|attach|renewal|default|loss)\s+rate\b",
                         self.name, re.I)
            or re.search(r"\brate\s*\(%\)|\brate\s*%", self.name, re.I)
            or self.name.strip().startswith("%"))
        if value_says_ratio or self.fmt in ("P", "B"):
            looks_ratio = True
        elif value_says_level:
            looks_ratio = False
        else:
            looks_ratio = bool(name_says_ratio)
        self.kind = "ratio" if looks_ratio else "level"
        if self.kind == "ratio":
            mags = [abs(v) for v in self.values if v is not None]
            self.pct_is_fraction = (bool(mags) and max(mags) <= 1.5
                                    and self.pct_votes == 0)
            self.unit = "bps"
        else:
            self.unit = "pct"

    def as_points(self):
        if self.kind == "pre_growth":
            return list(self.values)
        if self.kind == "ratio" and getattr(self, "pct_is_fraction", False):
            return [None if v is None else v * 100.0 for v in self.values]
        return list(self.values)

    def derive(self):
        vals = self.as_points()
        n = len(vals)

        nonnull = [v for v in vals if v is not None]
        self.negative_convention = (bool(nonnull) and all(v < 0 for v in nonnull)
                                    and self.kind != "pre_growth")
        if self.negative_convention:
            vals = [None if v is None else -v for v in vals]
            self.flags.append("reported_as_negative")

        peak = max((abs(v) for v in vals if v is not None), default=0.0)
        self.growth = [None] * n
        base_unstable_any = False

        denom = getattr(self, "flow_denominator", None)
        if denom is not None and self.kind != "pre_growth":
            dvals = denom.as_points()
            for t in range(n):
                if t < 1 or t >= len(dvals):
                    continue
                cur, base_stock = vals[t], dvals[t - 1]
                if cur is None or not base_stock:
                    continue
                self.growth[t] = cur / abs(base_stock) * 100.0
            self.flow_rate_mode = True
            self.accel = [None] * n
            for t in range(n):
                g, gp = self.growth[t], self.growth[t - 1] if t >= 1 else None
                if g is not None and gp is not None:
                    self.accel[t] = g - gp
            self.obs = sum(1 for v in vals if v is not None)
            self.accel_obs = [a for a in self.accel if a is not None]
            latest_mag = next((v for v in reversed(vals) if v is not None), None)
            self.latest_value = latest_mag
            self.latest_growth = self.growth[-1] if n else None
            self.latest_accel = self.accel[-1] if n else None
            self.prev_growth = self.growth[-2] if n >= 2 else None
            self._z()
            self._streak()
            self._comp_effect()
            self._step_series()
            self._label_quality()
            self._cumulative_series()
            self._quantised_source()
            self._volatile_base()
            return

        if self.kind == "pre_growth":
            self.growth = list(vals)
        for t in range(n):
            if self.kind == "pre_growth" or t < 4:
                continue
            cur, base = vals[t], vals[t - 4]
            if cur is None or base is None:
                continue
            if self.kind == "ratio":
                self.growth[t] = (cur - base) * 100.0
            else:
                if base == 0 or (peak and abs(base) < BASE_FLOOR_FRAC * peak):
                    base_unstable_any = True
                    continue
                if (cur < 0) != (base < 0):
                    base_unstable_any = True
                    continue
                self.growth[t] = (cur - base) / abs(base) * 100.0

        self.accel = [None] * n
        for t in range(n):
            g, gp = self.growth[t], self.growth[t - 1] if t >= 1 else None
            if g is not None and gp is not None:
                self.accel[t] = g - gp

        if base_unstable_any:
            self.flags.append("base_unstable")

        self.obs = sum(1 for v in vals if v is not None)
        self.accel_obs = [a for a in self.accel if a is not None]

        latest_mag = next((v for v in reversed(vals) if v is not None), None)
        self.latest_value = (-latest_mag if (self.negative_convention
                                            and latest_mag is not None)
                             else latest_mag)
        self.latest_growth = self.growth[-1] if n else None
        self.latest_accel = self.accel[-1] if n else None
        self.prev_growth = self.growth[-2] if n >= 2 else None

        self._z()
        self._streak()
        self._comp_effect()
        self._step_series()
        self._label_quality()
        self._cumulative_series()
        self._quantised_source()
        self._volatile_base()

    def _z(self):
        self.z = None
        self.z_conf = "none"
        hist = [a for a in self.accel[:-1] if a is not None]
        if self.latest_accel is None or len(hist) < MIN_ACCEL_OBS_FOR_Z:
            return
        mu = statistics.fmean(hist)
        sd = statistics.pstdev(hist)
        if sd <= 0:
            return
        self.z = (self.latest_accel - mu) / sd
        self.z_mean = mu
        self.z_sd = sd
        self.z_conf = "full" if self.obs >= MIN_Q_FOR_FULL_CONF else "low"

    def _streak(self):
        self.streak = 0
        self.streak_dir = "flat"
        self.streak_start = None
        self.streak_start_idx = None
        seq = [a for a in self.accel if a is not None]
        if not seq:
            return
        sign = 1 if seq[-1] > 0 else (-1 if seq[-1] < 0 else 0)
        if sign == 0:
            return
        c = 0
        for a in reversed(seq):
            if (a > 0 and sign > 0) or (a < 0 and sign < 0):
                c += 1
            else:
                break
        self.streak = c
        self.streak_dir = "accelerating" if sign > 0 else "decelerating"
        idx = [i for i, a in enumerate(self.accel) if a is not None]
        if idx and c <= len(idx):
            self.streak_start = self.periods[idx[-c]] if idx[-c] < len(self.periods) else None
            self.streak_start_idx = idx[-c]
        else:
            self.streak_start, self.streak_start_idx = None, None

    def _staleness(self, scan_quarter):
        self.scan_quarter = (scan_quarter or "").upper()
        last = (self.periods[-1] if self.periods else "") or ""
        self.stale = bool(self.scan_quarter) and last.upper() != self.scan_quarter
        if self.stale:
            self.flags.append("stale")

    def _label_quality(self):
        n = (self.name or "").strip()
        self.unnamed = bool(SCHEDULE_LABEL_PAT.match(n))
        self.unit_implausible = False
        if PER_UNIT_NAME_PAT.search(n) and self.latest_value is not None \
                and abs(self.latest_value) >= PER_UNIT_IMPLAUSIBLE:
            self.unit_implausible = True
        if self.unnamed:
            self.flags.append("unnamed_line")
        if self.unit_implausible:
            self.flags.append("per_unit_scale_implausible")
        vals = [v for v in (self.values or []) if v is not None]
        self.count_as_money = bool(
            self.kind == "level" and getattr(self, "currency", None) and vals
            and is_count_name(n)
            and all(abs(v) < 1e12 and any(abs(v / d - round(v / d)) < 1e-9 and abs(v / d) < 1e5
                                          for d in (1e6, 1e3)) for v in vals))
        if self.count_as_money:
            self.flags.append("count_quoted_as_money")

    def _cumulative_series(self):
        self.cumulative = False
        if self.kind != "level":
            return
        _pa = self._annual_period_test()
        if _pa:
            if _pa[0] == "year_to_date":
                self.cumulative = True
                self.flags.append("cumulative_series")
            return
        by_year = {}
        for lab, v in zip(self.periods or [], self.values or []):
            m = re.fullmatch(r"R?-?\s*([1-4])Q(\d{2})", str(lab or "").strip().upper())
            if not m or v is None:
                continue
            by_year.setdefault(m.group(2), {})[int(m.group(1))] = v
        full = [y for y in by_year.values() if all(q in y for q in (1, 2, 3, 4))]
        if len(full) < 2:
            return
        for y in full:
            q = [y[1], y[2], y[3], y[4]]
            if not (q[0] > 0 and q[1] > q[0] and q[2] > q[1] and q[3] > q[2]
                    and q[3] >= CUMULATIVE_Q4_OVER_Q1 * q[0]):
                return
        self.cumulative = True
        self.flags.append("cumulative_series")

    def _is_flow(self):
        code = str(self.ltcode or "")
        if self.kind != "level" or code.startswith(("BS_", "RT_")):
            return False
        if re.search(r"\bdays\b|balance|outstanding|\bheadcount\b", self.name or "", re.I):
            return False
        return code.startswith(("CF_", "IS_")) or self._is_cash_flow()

    def _is_cash_flow(self):
        return self.kind == "level" and bool(
            str(self.ltcode or "").startswith("CF_")
            or re.search(r"^ocf\b|^fcf\b|operating cash flow|free cash flow|capex|capital expend",
                         self.name or "", re.I))

    def _annual_period_test(self):
        ann = getattr(self, "annual", None) or {}
        if not ann:
            return None
        by_year = {}
        for lab, v in zip(self.periods or [], self.values or []):
            m = re.fullmatch(r"R?-?\s*([1-4])Q(\d{2})", str(lab or "").strip().upper())
            if m and v is not None:
                by_year.setdefault(m.group(2), {})[int(m.group(1))] = v
        cur = getattr(self, "currency", None)
        for yy in sorted(set(by_year) & set(ann), reverse=True):
            q = by_year[yy]
            if not all(k in q for k in (1, 2, 3, 4)):
                continue
            a = ann[yy]
            if not a:
                continue
            tot = q[1] + q[2] + q[3] + q[4]
            tol = max(ANNUAL_SUM_TOL * abs(a), 1e-9)
            if abs(tot - a) <= tol:
                return ("quarterly", "the four fiscal 20%s quarters add up to %s, the "
                                     "full-year figure" % (yy, _money(abs(a) if a < 0 else a, cur)))
            if abs(q[4] - a) <= ANNUAL_Q4_TOL * abs(a) and abs(tot - a) > tol:
                return ("year_to_date", "4Q%s equals the full-year 20%s figure (%s)"
                        % (yy, yy, _money(abs(a) if a < 0 else a, cur)))
        return None

    def period_basis(self):
        _pa = self._annual_period_test()
        if _pa:
            return _pa
        if getattr(self, "cumulative", False):
            return "year_to_date", "rises through every fiscal year and resets at the first quarter"
        by_year = {}
        for lab, v in zip(self.periods or [], self.values or []):
            m = re.fullmatch(r"R?-?\s*([1-4])Q(\d{2})", str(lab or "").strip().upper())
            if m and v is not None:
                by_year.setdefault(m.group(2), {})[int(m.group(1))] = v
        cur = getattr(self, "currency", None)
        _vals = [v for v in (self.values or []) if v is not None]
        _neg = bool(_vals) and all(v <= 0 for v in _vals)
        for yy in sorted(by_year, reverse=True):
            y = by_year[yy]
            if _neg:
                y = {k: abs(v) for k, v in y.items()}
            qs = sorted(y)
            for a, b in reversed(list(zip(qs, qs[1:]))):
                if b == a + 1 and y[b] < y[a]:
                    return ("quarterly", "%dQ%s (%s) is below %dQ%s (%s), which a "
                                         "year-to-date total cannot be"
                            % (b, yy, _money(y[b], cur), a, yy, _money(y[a], cur)))
        return "unverified", "rises within every fiscal year in the window"

    def _step_series(self):
        vals = [v for v in self.as_points() if v is not None]
        self.step_like = False
        if len(vals) < 6:
            return
        deltas = [vals[i] - vals[i - 1] for i in range(1, len(vals))]
        zeros = sum(1 for d in deltas if d == 0)
        if zeros / len(deltas) >= STEP_ZERO_DELTA_FRAC:
            self.step_like = True
            self.flags.append("step_series")

    def _volatile_base(self):
        self.volatile_base = False
        if self.kind != "level" or self.latest_growth is None:
            return
        vals = [abs(v) for v in self.as_points() if v]
        if len(vals) < MIN_Q_FOR_RANK or min(vals) <= 0:
            return
        self.level_range = max(vals) / min(vals)
        if (self.level_range >= VOLATILE_BASE_RANGE
                and abs(self.latest_growth) >= VOLATILE_BASE_GROWTH):
            self.volatile_base = True
            self.flags.append("volatile_base")

    def _quantised_source(self):
        self.quant_step = None
        if self.kind != "ratio":
            return
        vals = [v for v in self.as_points() if v is not None]
        if len(vals) < 6:
            return
        for step, name in ((1.0, 100.0), (0.5, 50.0), (0.1, 10.0)):
            if all(abs(v / step - round(v / step)) < 1e-9 for v in vals):
                self.quant_step = name
                break
        if not self.quant_step or self.quant_step < QUANT_MIN_STEP_BPS:
            return
        self.flags.append("quantised_source")

    def _comp_effect(self):
        self.comp_flag = None
        self.comp_evidence = None
        hist = [g for g in self.growth[:-1] if g is not None]
        if self.prev_growth is None or len(hist) < 4:
            return
        q1, q3 = _quartile(hist, 0.25), _quartile(hist, 0.75)
        if self.latest_accel is None:
            return
        if self.latest_accel > 0 and q1 is not None and self.prev_growth <= q1:
            self.comp_flag = "easy_base"
            self.comp_evidence = {"prior_growth": self.prev_growth, "quartile": q1,
                                  "which": "bottom", "observations": len(hist)}
        elif self.latest_accel < 0 and q3 is not None and self.prev_growth >= q3:
            self.comp_flag = "hard_base"
            self.comp_evidence = {"prior_growth": self.prev_growth, "quartile": q3,
                                  "which": "top", "observations": len(hist)}

    def forward_bar(self):
        if getattr(self, "flow_rate_mode", False):
            return {"withheld": "this line is measured as a rate against "
                                "%s, so a percentage continuation threshold on "
                                "the flow itself would not be interpretable"
                                % getattr(self, "flow_denominator_name", "its stock")}
        """What next quarter has to deliver for this trajectory to hold.

        The year-ago base for the NEXT quarter is already known: it is the value
        four quarters before it, which is three quarters back from here. So the
        comp is not a matter of opinion, and two numbers fall straight out of it:
        the level next quarter needs in order to hold the current growth rate,
        and the growth rate that results if the level merely repeats this
        quarter. The gap between them is exactly how much of next quarter's
        growth the comparison will do by itself, which is the question every
        "is this durable" argument is really about.
        """
        vals = self.as_points()
        if len(vals) < 4 or self.latest_value is None:
            return None
        base = vals[-4]
        if base is None:
            return None
        period = self.periods[-4] if len(self.periods) >= 4 else None
        out = {"next_base_period": period, "next_base_value": base}
        if self.kind == "ratio":
            if self.latest_growth is None:
                return None
            out["level_to_hold_growth"] = base + self.latest_growth / 100.0
            out["growth_if_level_flat"] = (self.latest_value - base) * 100.0
        elif self.kind == "pre_growth":
            return None
        else:
            if base == 0 or self.latest_growth is None:
                return None
            out["level_to_hold_growth"] = base * (1.0 + self.latest_growth / 100.0)
            out["growth_if_level_flat"] = (self.latest_value - base) / abs(base) * 100.0
        if out.get("growth_if_level_flat") is not None and self.latest_growth is not None:
            out["comp_contribution"] = out["growth_if_level_flat"] - self.latest_growth
        if out.get("level_to_hold_growth") is not None:
            _, div, dp, _, _ = self.unit_info
            out["level_to_hold_display"] = round(
                out["level_to_hold_growth"] / (div or 1.0), dp if dp is not None else 2)
        return out

    @property
    def direction_note(self):
        if self.latest_accel is None or self.latest_growth is None:
            return None
        if self.latest_accel > 0 and self.latest_growth < 0:
            return "improving_from_decline"
        if self.latest_accel < 0 and self.latest_growth > 0:
            return "slowing_but_growing"
        return None

    @property
    def significant(self):
        if self.latest_accel is None:
            return False
        floor = MIN_ABS_ACCEL_BPS if self.unit == "bps" else MIN_ABS_ACCEL_PP
        return abs(self.latest_accel) >= floor

    @property
    def large_move(self):
        if self.latest_accel is None:
            return False
        floor = MIN_ABS_ACCEL_BPS if self.unit == "bps" else MIN_ABS_ACCEL_PP
        return abs(self.latest_accel) >= floor * LARGE_MOVE_MULT

    @property
    def material_inflection(self):
        if self.z is None or self.latest_accel is None:
            return False
        return abs(self.z) >= MATERIAL_Z and self.significant

    @property
    def primary_context(self):
        if self.material_inflection:
            return False
        if self.z is None or self.latest_accel is None:
            return False
        return (self.significant and self.large_move
                and self.tier <= 2)

    @property
    def qualifies(self):
        return self.material_inflection or self.primary_context

    @property
    def qualified_by(self):
        if self.material_inflection:
            return "material inflection: unusual and economically significant"
        if self.primary_context:
            return "primary context: large move, statistically routine for this line"
        return None

    @property
    def modelled(self):
        return any(MODELLED_SOURCE_PAT.search(src or "") for src in self.sources)

    @property
    def repeats_year_ago(self):
        v = [x for x in (self.values or [])]
        if self.kind != "level" or len(v) < 5 or v[-1] in (None, 0) or v[-5] is None:
            return False
        if v[-1] != v[-5]:
            if len(v) >= 6 and v[-2] not in (None, 0) and v[-6] is not None and v[-2] == v[-6] \
                    and abs(v[-2]) >= 1e6 and v[-2] != round(v[-2], -6):
                return True
            return False
        if _sig_digits(v[-1]) < COPY_MIN_SIG_DIGITS:
            return False
        mid = [x for x in v[-4:-1] if x is not None]
        return bool(mid) and any(x != v[-1] for x in mid)

    @property
    def rankable(self):
        return (
            not self.repeats_year_ago
            and not getattr(self, "twin_collapsed_by", None)
            and not getattr(self, "spliced_with", None)
            and not getattr(self, "shadowed_by", None)
            and not getattr(self, "modelled_input", False)
            and not getattr(self, "erratic_stock", False)
            and not getattr(self, "outside_parts", None)
            and not getattr(self, "vendor_ratio_mismatch", None)
            and self.z is not None
            and not self.modelled
            and self.obs >= MIN_Q_FOR_RANK
            and "base_unstable" not in self.flags
            and not getattr(self, "volatile_base", False)
            and not getattr(self, "stale", False)
            and not getattr(self, "step_like", False)
            and not getattr(self, "unnamed", False)
            and not getattr(self, "unit_implausible", False)
            and not getattr(self, "count_as_money", False)
            and not getattr(self, "cumulative", False)
            and not self.cfo_component()
            and self.latest_accel is not None
        )

    def cfo_component(self):
        nm = (self.name or "").strip()
        if not str(self.ltcode or "").startswith("CF_"):
            return False
        if not re.search(r"\s-\s*cfo\s*$", nm, re.I):
            return False
        return not re.search(r"net cash|operating activit|cash flows? from operat|^cfo\b|^ocf\b", nm, re.I)

    def small_base(self):
        if self.kind not in ("level", "ratio") or len(self.values) < 10:
            return None
        pts = self.as_points()
        out = None
        for off, gi, which in ((-5, -1, "current"), (-6, -2, "prior")):
            try:
                base, g = pts[off], self.growth[gi]
            except IndexError:
                continue
            if base is None or g is None:
                continue
            L = len(pts)
            idx = L + off
            around = [abs(pts[j]) for j in (idx - 8, idx - 4, idx + 4, idx + 8)
                      if 0 <= j < L - 1 and j != idx and pts[j] is not None]
            if len(around) < 2:
                continue
            typ = sorted(around)[(len(around) - 1) // 2]
            big = abs(g) >= (50.0 if self.kind == "level" else 1000.0)
            if self.kind == "ratio" and (base < 0 or min(v for v in pts[-13:-1] if v is not None) < 0):
                continue
            if typ > 0 and abs(base) < 0.35 * typ and big:
                def _d(v):
                    try:
                        _lab, _div, _dp, _st = self.unit_info[:4]
                        if self.kind == "level" and _lab not in (None, "currency"):
                            v = v / _div
                        return format_row_level(v, _lab, _dp, _st == "conflicted",
                                                getattr(self, "currency", None))
                    except Exception:
                        return None
                _sg = -1.0 if (base < 0) else 1.0
                out = {"which": which, "kind": self.kind, "base_period": self.periods[off],
                       "base_display": _d(base), "typical_display": _d(_sg * typ)}
                break
        return out

    def year_ago_comparison(self):
        if self.kind not in ("level", "ratio") or len(self.values) < 9 or len(self.growth) < 5:
            return None
        g_ya = self.growth[-5]
        hist = [g for g in self.growth[:-1] if g is not None]
        if g_ya is None or len(hist) < 6:
            return None
        q1, q3 = _quartile(hist, 0.25), _quartile(hist, 0.75)
        _lv = self.kind == "level"
        which = "weak" if (g_ya < 0 and ((q1 is not None and g_ya <= q1) or (_lv and g_ya <= -20.0))) else (
            "strong" if (g_ya > 0 and ((q3 is not None and g_ya >= q3) or (_lv and g_ya >= 40.0))) else None)
        if which is None:
            return None
        v2, v0 = self.as_points()[-9], self.as_points()[-1]
        if v2 is None or v0 is None:
            return None
        if self.kind == "level":
            if v2 <= 0:
                return None
            two = (v0 / v2 - 1.0) * 100.0
            two_disp = _fmt_growth(two, "pct")
        else:
            two = (v0 - v2) * 100.0
            two_disp = _fmt_growth(two, "bps")
        lvl = None
        try:
            _lab, _div, _dp, _st = self.unit_info[:4]
            _v = self.values[-9]
            if _v is not None:
                _v = -_v if self.negative_convention else _v
                if _lab not in (None, "currency"):
                    _v = _v / _div
                lvl = format_row_level(_v if self.kind == "level" else v2, _lab, _dp,
                                       _st == "conflicted", getattr(self, "currency", None))
        except Exception:
            lvl = None
        return {"which": which, "year_ago_period": self.periods[-5],
                "year_ago_growth": g_ya, "year_ago_growth_display": _fmt_growth(g_ya, self.unit),
                "two_year_period": self.periods[-9], "two_year_change": two,
                "two_year_change_display": two_disp, "two_year_level_display": lvl}

    @property
    def lower_is_better(self):
        return self.polarity == -1

    def level_word(self):
        g = self._level_move()
        if g is None or g == 0:
            return None
        if self.kind == "pre_growth":
            return "rising" if g > 0 else "falling"
        if self.kind == "ratio":
            if self.lower_is_better:
                return "improving" if g < 0 else "rising"
            if re.search(r"margin|ratio", "%s %s" % (self.name or "", getattr(self, "display", "") or ""),
                         re.I):
                return "widening" if g > 0 else "narrowing"
            return "rising" if g > 0 else "falling"
        return "growing" if g > 0 else "falling"

    def _level_move(self):
        if self.kind == "pre_growth":
            v = [x for x in (self.values or [])]
            if len(v) >= 5 and v[-1] is not None and v[-5] is not None:
                return v[-1] - v[-5]
            return None
        return self.latest_growth

    def pace_only_tag(self, imp):
        g = self._level_move()
        pol = self.polarity
        if imp not in ("Favourable", "Unfavourable") or g is None or g == 0 or not pol:
            return None
        level_good = (g > 0) == (pol > 0)
        if level_good == (imp == "Favourable"):
            return None
        w = self.level_word()
        return ("still " + w) if w else None

    @property
    def display(self):
        return getattr(self, "_display_override", None) or display_name(self.name)

    @property
    def prior_year_display(self):
        vals = self.as_points()
        if len(vals) < 5 or vals[-5] is None:
            return None
        lab, div, _dp, _st, _how = self.unit_info
        v = -vals[-5] if self.negative_convention else vals[-5]
        if lab in (None, "currency"):
            return v
        return v / div

    @property
    def absolute_change(self):
        cur, prior = self.display_level[0], self.prior_year_display
        if cur is None or prior is None:
            return None
        return cur - prior

    @property
    def level_direction(self):
        g = self.latest_growth
        if g is None or getattr(self, "flow_rate_mode", False):
            return "flat"
        if abs(g) < 0.1:
            return "flat"
        return "up" if g > 0 else "down"

    @property
    def momentum_direction(self):
        a = self.latest_accel
        if a is None:
            return "flat"
        if abs(a) < 1e-9:
            return "flat"
        return "up" if a > 0 else "down"

    @property
    def unit_info(self):
        if getattr(self, "identity_failed", None):
            return (None, 1.0, 2, "conflicted", self.identity_failed)
        if getattr(self, "identity_unit", None):
            return (self.identity_unit, 1.0 / self.identity_multiplier, 2,
                    "validated", self.identity_basis)
        return unit_status(self.name, self.kind,
                           getattr(self, "currency_votes", 0),
                           getattr(self, "multiple_votes", 0) > 0,
                           any_currency=bool(getattr(self, "currency", None)))

    @property
    def display_level(self):
        lab, div, dp, status, _ = self.unit_info
        if self.latest_value is None:
            return None, lab, dp, status
        v = -self.latest_value if self.negative_convention else self.latest_value
        if lab in (None, "currency"):
            return v, lab, dp, status
        return v / div, lab, dp, status

    def segment_revenue_share(self, rev_value):
        if not rev_value or self.latest_value is None or self.kind != "level":
            return None
        if not (self.category or "").upper().startswith("SD"):
            return None
        _ = self.polarity
        if not (SEGMENT_REV_PAT.search(self.name or "") or getattr(self, "_product_revenue", False)):
            return None
        return abs(self.latest_value) / abs(rev_value)

    def _tier_name(self):
        _ = self.polarity
        if SEGMENT_REV_PAT.search(self.name or "") or not getattr(self, "_product_revenue", False):
            return self.name
        return (self.name or "") + " - revenue"

    def tier_with(self, rev_value=None):
        share = None
        if rev_value and self.latest_value is not None and self.kind == "level":
            share = abs(self.latest_value) / abs(rev_value)
        return tier_of(self._tier_name(), self.tags, share, self.category,
                       bool(getattr(self, "segment_scope", None)))

    def tier_basis_with(self, rev_value=None):
        share = None
        if rev_value and self.latest_value is not None and self.kind == "level":
            share = abs(self.latest_value) / abs(rev_value)
        return tier_basis(self._tier_name(), self.tags, share, self.category,
                          bool(getattr(self, "segment_scope", None)))

    @property
    def tier(self):
        if getattr(self, "_tier_override", None):
            return self._tier_override
        return tier_of(self.name, self.tags, None, None,
                       bool(getattr(self, "segment_scope", None)))

    @property
    def polarity(self):
        if getattr(self, "_mix_share", False):
            self.polarity_state, self.polarity_source = "neutral", "mix_share:complement"
            return 0
        if getattr(self, "_polarity_override", None) is not None:
            self.polarity_state = getattr(self, "polarity_state", None) or "declared"
            self.polarity_source = "override:pair"
            return self._polarity_override
        pol, self.polarity_state, self.polarity_source = resolve_polarity(
            self.name, self.ltcode, getattr(self, "template", None),
            facts={"kind": self.kind, "category": self.category,
                   "currency": getattr(self, "currency", None),
                   "negative_convention": getattr(self, "negative_convention", False)})
        if self.polarity_source == "shape:segment_product_revenue":
            self._product_revenue = True
        return pol

    @property
    def confidence(self):
        if self.z is None or not self.rankable:
            return "Withheld"
        gaps = 0
        if self.periods:
            gaps = max(0, LOOKBACK_QUARTERS - self.obs)
        score = 0
        score += 2 if self.obs >= MIN_Q_FOR_FULL_CONF else (1 if self.obs >= MIN_Q_FOR_RANK else 0)
        score += 1 if gaps <= 2 else 0
        prov = self.provenance
        score += 2 if prov == "filed" else (1 if prov == "derived" else 0)
        score += 0 if self.comp_flag else 1
        score -= 1 if "step_series" in self.flags else 0
        score -= 2 if "quantised_source" in self.flags else 0
        lab, _div, _dp, status, _how = self.unit_info
        if status == "validated" and getattr(self, "identity_unit", None):
            return "Moderate" if score >= 3 else "Low"
        if score >= 5:
            return "High"
        if score >= 3:
            return "Moderate"
        return "Low"

    def implication(self, rev=None):
        if self.latest_accel is None:
            return "Ambiguous", "no measurable change"
        if self.kind == "pre_growth" and abs(self.latest_accel) < 1e-9:
            return "Ambiguous", "the reading is unchanged from last quarter"
        if getattr(self, "_mix_share", False):
            return ("Ambiguous", "a mix share whose complement is also reported: one "
                    "part rising is the other falling, and which is better depends on why")
        up = self.latest_accel > 0
        pol = self.polarity

        if (rev is not None and self.kind == "level" and self.latest_growth is not None
                and rev.latest_growth is not None
                and WORKING_CAPITAL_PAT.search(self.name or "")
                and not NOT_WORKING_CAPITAL_PAT.search(self.name or "")
                and self.latest_growth - rev.latest_growth > 10.0):
            return "Unfavourable", (
                "growing %.1f percentage points faster than revenue, which grew "
                "%.1f%% year over year" % (self.latest_growth - rev.latest_growth,
                                           rev.latest_growth))

        if pol == 0:
            return "Ambiguous", (polarity_note(self.name)
                                 or "direction is context dependent for this line")
        favourable = (up and pol > 0) or (not up and pol < 0)
        if (rev is not None and rev is not self and self.kind == "level" and pol < 0
                and self.latest_growth is not None and rev.latest_growth is not None
                and COST_OF_REVENUE_PAT.search(self.name or "")):
            gap = self.latest_growth - rev.latest_growth
            if favourable and gap > 0.05:
                return "Ambiguous", (
                    "slowed, but still grew faster than revenue (%+.1f%% against %+.1f%%), "
                    "so the margin it feeds still narrowed" % (self.latest_growth, rev.latest_growth))
            if not favourable and gap < -0.05:
                return "Ambiguous", (
                    "sped up, but still grew slower than revenue (%+.1f%% against %+.1f%%), "
                    "so the margin it feeds still widened" % (self.latest_growth, rev.latest_growth))
        _subj = re.sub(r"^.*?%\s*revenue\s*-\s*", "", self.name or "", flags=re.I)
        _subj = re.sub(r"\s*as\s*%\s*of\s*revenue\s*$", "", _subj, flags=re.I)
        if (self.kind == "ratio" and pol < 0 and self.latest_growth is not None
                and re.search(r"%\s*revenue|as % of revenue", self.name or "", re.I)
                and polarity_of(_subj) == -1
                and not re.search(r"inventor|receivab|payabl|\bdebt\b|leverage|\bdays\b",
                                  _subj, re.I)):
            if favourable and self.latest_growth > 0.5:
                return "Ambiguous", ("rose less than last quarter, but is still %+d bps above a year "
                                     "ago, so the margin it feeds still narrowed" % round(self.latest_growth))
            if not favourable and self.latest_growth < -0.5:
                return "Ambiguous", ("fell less than last quarter, but is still %+d bps below a year "
                                     "ago, so the margin it feeds still widened" % round(self.latest_growth))
        if self.comp_flag:
            return ("Favourable" if favourable else "Unfavourable",
                    ("higher is generally favourable for this line, and the "
                     "prior-year base is doing part of the move")
                    if pol > 0 else
                    ("lower is generally favourable for this line, and the "
                     "prior-year base is doing part of the move"))
        return ("Favourable" if favourable else "Unfavourable",
                "higher is generally favourable for this line" if pol > 0
                else "lower is generally favourable for this line")

    def attribution(self, context=None):
        ctx = context or {}
        labels = []
        if "step_series" in self.flags:
            labels.append("Accounting or definition change")
        ev = self.comp_evidence or {}
        if self.comp_flag in ("easy_base", "hard_base") and ev:
            unit = "%" if self.unit == "pct" else " bps"
            _d = 1 if self.unit == "pct" else 0
            if self.kind == "pre_growth":
                labels.append(
                    f"Part of the change is the comparison: last quarter's reading of "
                    f"{ev['prior_growth']:,.1f}% was in the {ev['which']} quarter of this "
                    f"line's last {ev['observations']} readings")
            else:
                labels.append(
                    f"Part of the change is the comparison: last quarter's y/y of "
                    f"{ev['prior_growth']:+,.{_d}f}{unit} was in the {ev['which']} quarter of "
                    f"this line's last {ev['observations']} readings")
        if ctx.get("acquisition_material") and self.kind == "level":
            labels.append("Acquisition activity is material in the period")
        if ctx.get("currency_material") and self.kind == "level":
            labels.append("A disclosed currency contribution is material in the period")
        if not labels:
            labels.append("No disclosed breakdown of this line to attribute the move to")
        return labels

    def economic_impact(self, rev_value=None):
        if not rev_value or self.latest_accel is None:
            return None, None, "not comparable"
        impact = None
        if self.kind == "ratio" and self.unit == "bps":
            if (self.latest_growth is not None
                    and REVENUE_DENOMINATED_PAT.search(self.name or "")
                    and not getattr(self, "segment_scope", None)):
                impact = abs(self.latest_growth) / 10000.0 * abs(rev_value)
        elif self.kind == "level":
            lab, _div, _dp, status, _how = self.unit_info
            if lab in (None, "currency") and status != "conflicted":
                vals = self.as_points()
                if len(vals) >= 5 and vals[-1] is not None and vals[-5] is not None:
                    impact = abs(abs(vals[-1]) - abs(vals[-5]))
        if impact is None:
            return None, None, "not comparable"
        share = impact / abs(rev_value)
        band = ("high" if share >= 0.05 else
                "moderate" if share >= 0.01 else
                "low" if share >= 0.0025 else "immaterial")
        return impact, share, band

    def priority(self, rev_value=None):
        if self.z is None:
            return 0.0, {}
        unusual = min(abs(self.z) / 3.0, 1.0)
        tier = TIER_SCORE.get(self.tier_with(rev_value), 0.25)
        _imp, _share, _band = self.economic_impact(rev_value)
        if _share is not None:
            material = min(_share / 0.05, 1.0)
        elif rev_value and self.kind == "level" and self.latest_value is not None:
            share = abs(self.latest_value) / abs(rev_value)
            material = 0.5 if share < 1e-4 else min(share / 0.25, 1.0)
        elif self.kind in ("ratio", "pre_growth"):
            material = 0.75
        else:
            material = 0.5
        conf = CONF_SCORE.get(self.confidence, 0.3)
        score = (W_UNUSUAL * unusual + W_TIER * tier
                 + W_MATERIAL * material + W_CONFIDENCE * conf)

        tr = template_rank(getattr(self, "template", None), self.name)
        bump = 0.0
        if tr is not None:
            bump += TEMPLATE_BONUS * (1.0 - tr / max(len(TEMPLATE_PRIORITY.get(
                getattr(self, "template", None)) or [1]), 1))
        if SEGMENT_DETAIL_PAT.search(self.name or ""):
            bump -= SEGMENT_DETAIL_PENALTY
        _t = getattr(self, "template", None)
        if _t and re.search(r"\bebitda\b", self.name or "", re.I) and not any(
                re.search(r"ebitda", p.pattern, re.I) for p in (TEMPLATE_PRIORITY.get(_t) or [])):
            bump -= EBITDA_OFF_TEMPLATE_PENALTY
        total = score + bump
        return total, {"unusualness": round(unusual, 3), "tier_weight": round(tier, 3),
                       "materiality": round(material, 3), "confidence_weight": round(conf, 3),
                       "economic_impact": (round(_imp, 2) if _imp is not None else None),
                       "economic_share_of_revenue": (round(_share, 5)
                                                     if _share is not None else None),
                       "economic_band": _band,
                       "composite": round(score, 4),
                       "template_rank": tr,
                       "template_adjustment": round(bump, 4),
                       "adjusted_total": round(total, 4)}

    @property
    def provenance(self):
        if getattr(self, "_company_published", False):
            return "filed"
        if not self.sources:
            return None
        worst, rank = None, -1
        for src in self.sources:
            label, r = SOURCE_QUALITY.get(src.strip().lower(), ("other", 2))
            if r > rank:
                worst, rank = label, r
        return worst

    @property
    def headline_tier(self):
        t = (self.tags or "").lower()
        return any(k in t for k in ("top_kpi", "earnings_flash_kpi",
                                    "company_kpi", "driver_kpi"))

    UNUSUAL_BAND = ((2.5, "very unusual"), (1.5, "unusual"),
                    (MATERIAL_Z, "notable"), (0.0, "statistically routine"))

    def assessment(self, rev=None, rev_value=None, context=None):
        z = self.z
        band = "not scoreable"
        if z is not None:
            for cut, lab in self.UNUSUAL_BAND:
                if abs(z) >= cut:
                    band = lab
                    break
        imp, imp_why = self.implication(rev)
        _impact, _share, _eband = self.economic_impact(rev_value)
        attrib = self.attribution(context)
        unresolved = any(str(a).startswith("Unresolved") for a in attrib)
        return {
            "statistical_unusualness": {
                "z": _clamp_display(z), "band": band,
                "clears_statistical_bar": (z is not None and abs(z) >= MATERIAL_Z)},
            "economic_materiality": {
                "impact": (round(_impact, 2) if _impact is not None else None),
                "share_of_revenue": (round(_share, 5) if _share is not None else None),
                "band": _eband,
                "clears_own_units_bar": bool(self.significant),
                "large_on_economics_alone": bool(self.large_move and not (
                    z is not None and abs(z) >= MATERIAL_Z))},
            "relevance": {"tier": self.tier_with(rev_value),
                          "tier_label": {1: "primary", 2: "supporting",
                                         3: "contextual"}.get(
                                             self.tier_with(rev_value), "contextual"),
                          "sector_template_rank": template_rank(
                              getattr(self, "template", None), self.name)},
            "business_implication": {"verdict": imp, "reason": imp_why,
                                     "lower_is_better": bool(self.lower_is_better)},
            "cross_metric": {"status": "not assessed", "pairs": []},
            "confidence": {
                "source": self.confidence,
                "provenance_class": self.provenance or "not returned",
                "explanation": ("unresolved driver" if unresolved
                                else "supported explanation"),
                "explanation_basis": attrib,
                "commentary": _commentary_standing(self, context)},
        }

    def adjustment_effect(self, rev=None, context=None):
        ctx = (context or {}).get("commentary") or {}
        if not isinstance(ctx, dict):
            return None
        need = ("text", "speaker", "role", "quarter", "document", "document_date", "document_id")

        def _ok(q):
            return (isinstance(q, dict) and isinstance(q.get("effect"), dict)
                    and not any(not str(q.get(f) or "").strip() for f in need)
                    and not (self.periods and str(q.get("quarter")).upper() != str(self.periods[-1]).upper()))

        def _one(q, carried):
            e = q["effect"]
            try:
                size = float(e.get("size"))
            except (TypeError, ValueError):
                return None
            unit = str(e.get("unit") or "").lower()
            kind = str(e.get("kind") or "one-time").lower()
            if kind not in ("one-time", "policy", "acquisition"):
                kind = "one-time"
            move = self.latest_growth
            eff_bps, share, size_display = None, None, None
            if unit == "bps" and self.kind == "ratio":
                eff_bps = size
                size_display = "about %s bps" % format(int(round(size)), ",")
            elif unit == "usd" and self.kind == "ratio" and rev is not None and rev.latest_value:
                eff_bps = size / abs(rev.latest_value) * 1e4
                size_display = "about %s (about %s bps of revenue)" % (_money(size, getattr(rev, "currency", None) or "$"),
                                                                       format(int(round(eff_bps)), ","))
            elif unit == "usd" and self.kind == "level":
                size_display = "about " + _money(size, getattr(self, "currency", None) or "$")
                v = self.as_points()
                if len(v) >= 5 and v[-1] is not None and v[-5] is not None and v[-1] != v[-5]:
                    share = size / (v[-1] - v[-5])
            elif unit == "usd_per_share":
                size_display = "about $%.2f a share" % size
            if size_display is None:
                return None
            charge = str(e.get("direction") or "benefit").lower() == "charge"
            if eff_bps is not None and move:
                share = (-eff_bps if charge else eff_bps) / move
            elif share is not None and charge:
                share = -share
            ex_level = ex_move = None
            if eff_bps is not None and self.kind == "ratio" and self.latest_value is not None \
                    and move is not None:
                _sg = -1.0 if charge else 1.0
                _lv = self.latest_value * (100.0 if getattr(self, "pct_is_fraction", False) else 1.0)
                ex_level = "%.2f%%" % (_lv - _sg * eff_bps / 100.0)
                ex_move = "%+d bps" % int(round(move - _sg * eff_bps))
                ex_move = ("+" if not ex_move.startswith("-") else "-") + format(abs(int(round(move - _sg * eff_bps))), ",") + " bps"
            return {"label": str(e.get("label") or "a one-time item"), "kind": kind,
                    "ex_level_display": ex_level, "ex_move_display": ex_move,
                    "charge": charge, "carried_from": ("gross margin" if carried else None),
                    "size": size, "unit": unit, "size_display": size_display,
                    "effect_bps": round(eff_bps) if eff_bps is not None else None,
                    "share_of_move": round(share, 3) if share is not None else None,
                    "document": q.get("document"), "quarter": q.get("quarter"),
                    "speaker": q.get("speaker"), "role": q.get("role"),
                    "page": q.get("page") or ""}

        own = _one(ctx[self.ltcode], False) if _ok(ctx.get(self.ltcode)) else None
        car = None
        if self.kind == "ratio" and re.match(r"^IS_P_1(09|10|13|14)-M$", str(self.ltcode or "")):
            for _gc in ("IS_P_105-M", "IS_P_106-M"):
                if _ok(ctx.get(_gc)) and str(ctx[_gc]["effect"].get("unit")) in ("bps", "usd"):
                    car = _one(ctx[_gc], True)
                    break
        if own and car:
            own["also"] = car
            if own.get("effect_bps") is not None and car.get("effect_bps") is not None \
                    and self.latest_value is not None and self.latest_growth is not None:
                _net = (-1.0 if own.get("charge") else 1.0) * own["effect_bps"] \
                    + (-1.0 if car.get("charge") else 1.0) * car["effect_bps"]
                _lv = self.latest_value * (100.0 if getattr(self, "pct_is_fraction", False) else 1.0)
                own["ex_level_display"] = "%.2f%%" % (_lv - _net / 100.0)
                _m = int(round(self.latest_growth - _net))
                own["ex_move_display"] = ("-" if _m < 0 else "+") + format(abs(_m), ",") + " bps"
                own["ex_both"] = True
                own["share_net"] = round(_net / self.latest_growth, 3) if self.latest_growth else None
            car["ex_level_display"] = car["ex_move_display"] = None
            return own
        return own or car

    def row(self, rev=None, rev_value=None, context=None):
        imp, imp_why = self.implication(rev)
        score, comp = self.priority(rev_value)
        _row = {
            "adjustment_effect": self.adjustment_effect(rev, context),
            "assessment": self.assessment(rev, rev_value, context),
            "economic_impact_display": (
                _money(self.economic_impact(rev_value)[0])
                if self.economic_impact(rev_value)[0] is not None
                and self.kind == "ratio" else None),
            "ltcode": self.ltcode,
            "name": self.name,
            "display": self.display,
            "period_basis": (self.period_basis()[0] if self._is_cash_flow() and self._is_flow() else None),
            "period_evidence": (self.period_basis()[1] if self._is_cash_flow() and self._is_flow() else None),
            "tier": self.tier_with(rev_value),
            "polarity": self.polarity,
            "polarity_state": getattr(self, "polarity_state", None),
            "polarity_source": getattr(self, "polarity_source", None),
            "confidence": self.confidence,
            "implication": imp,
            "implication_reason": imp_why,
            "pace_only": self.pace_only_tag(imp),
            "basis_used": getattr(self, "basis_used", None),
            "level_word": self.level_word(),
            "attribution": self.attribution(context),
            "priority": round(score, 4),
            "priority_components": comp,
            "category": self.category,
            "kind": self.kind,
            "unit": self.unit,
            "tags": self.tags,
            "scale": self.scale,
            "quarters": self.obs,
            "latest_period": self.periods[-1] if self.periods else None,
            "latest_value": self.latest_value,
            "latest_growth": self.latest_growth,
            "prev_growth": self.prev_growth,
            "latest_accel": self.latest_accel,
            "z": self.z,
            "z_display": _clamp_display(self.z),
            "z_bin": _bin_z(self.z),
            "z_confidence": self.z_conf,
            "streak": self.streak,
            "streak_dir": self.streak_dir,
            "level_direction": self.level_direction,
            "momentum_direction": self.momentum_direction,
            "level_vs_momentum": ("diverging"
                                  if (self.level_direction != "flat"
                                      and self.momentum_direction != "flat"
                                      and self.level_direction != self.momentum_direction)
                                  else "aligned"),
            "template": getattr(self, "template", None),
            "signal_role": getattr(self, "signal_role", "primary"),
            "derived_from": getattr(self, "derived_from", None),
            "flow_rate_mode": bool(getattr(self, "flow_rate_mode", False)),
            "flow_denominator_name": getattr(self, "flow_denominator_name", None),
            "comp_flag": self.comp_flag,
            "comp_evidence": self.comp_evidence,
            "direction_note": self.direction_note,
            "provenance": self.provenance,
            "uic": self.uic,
            "source_mix": dict(sorted(self.sources.items())),
            "streak_start": self.streak_start,
            "forward_bar": self.forward_bar(),
            "flags": self.flags,
            "segment_scope": getattr(self, "segment_scope", None),
            "currency": getattr(self, "currency", None),
            "tier_basis": self.tier_basis_with(rev_value),
            "segment_revenue_share": self.segment_revenue_share(rev_value),
            "quant_step_bps": getattr(self, "quant_step", None),
            "qualified_by": self.qualified_by,
            "unit_label": self.unit_info[0],
            "unit_divisor": self.unit_info[1],
            "unit_decimals": self.unit_info[2],
            "unit_status": self.unit_info[3],
            "unit_source": self.unit_info[4],
            "pct_source_marked": bool(getattr(self, "pct_votes", 0)),
            "pct_read_as_fraction": bool(getattr(self, "pct_is_fraction", False)),
            "basis_qualifier": basis_qualifier(self.name),
            "display_value": self.display_level[0],
            "prior_year_value": self.prior_year_display,
            "absolute_change": self.absolute_change,
            "lower_is_better": self.lower_is_better,
            "growth_series": self.growth,
            "accel_series": self.accel,
            "value_series": self.as_points(),
            "period_series": self.periods,
            "calculated_twin": getattr(self, "calculated_twin", None),
            "year_ago_comparison": self.year_ago_comparison(),
            "small_base": self.small_base(),
            "insync_calculated": bool(getattr(self, "insync_calculated", False)),
        }
        if getattr(self, "polarity_state", None) == "inferred":
            _row["polarity_basis"] = "inferred"
            _row["polarity_basis_note"] = ("read assuming the line is revenue: the name "
                                          "carries no cost word and no rule declares it")
        return _row



QUARTER_DAYS = 91.3125
GRID_DATE_TOL_DAYS = 20
FISCAL_LABEL_PAT = re.compile(r"^(?:R\d*-)?([1-4])Q(\d{2})$", re.I)
REVISION_PREFIX_PAT = re.compile(r"^R\d*-", re.I)


def clean_period_label(label):
    return REVISION_PREFIX_PAT.sub("", str(label or "").strip())


def _label_index(label):
    m = FISCAL_LABEL_PAT.match(clean_period_label(label))
    if not m:
        return None
    return int(m.group(2)) * 4 + int(m.group(1)) - 1


def _index_label(i):
    return "%dQ%02d" % (i % 4 + 1, (i // 4) % 100)


def _to_date(qed):
    try:
        return datetime.strptime(str(qed)[:10], "%Y-%m-%d")
    except ValueError:
        return None


def _normalise_period_labels(raw):
    for key in ("actuals", "guidance", "outlook", "annual_actuals", "call_dates"):
        for r in raw.get(key) or []:
            if not isinstance(r, dict):
                continue
            for f in ("FISCALPERIOD", "fiscal_period", "SOURCE_PERIOD", "source_period"):
                if r.get(f) is not None:
                    r[f] = clean_period_label(r[f])
    if raw.get("latest_reported_quarter"):
        raw["latest_reported_quarter"] = clean_period_label(raw["latest_reported_quarter"])


def _quarter_grid(points):
    info = {"missing_labels": [], "misaligned": [], "label_disagrees": [],
            "duplicate_slot": []}
    dated = []
    for qed in sorted(points):
        d = _to_date(qed)
        fp, val = points[qed]
        dated.append((qed, d, clean_period_label(fp) or qed, val))
    usable = [x for x in dated if x[1] is not None]
    if not usable or len(usable) != len(dated):
        return ([v for _, _, _, v in dated], [f for _, _, f, _ in dated], info)
    anchor_qed, anchor_d, anchor_fp, _ = usable[-1]
    anchor_idx = _label_index(anchor_fp)
    slots = {}
    for qed, d, fp, val in usable:
        days = (anchor_d - d).days
        k = int(round(days / QUARTER_DAYS))
        if abs(days - k * QUARTER_DAYS) > GRID_DATE_TOL_DAYS:
            info["misaligned"].append(qed)
        li = _label_index(fp)
        if anchor_idx is not None and li is not None and anchor_idx - li != k:
            info["label_disagrees"].append({"date": qed, "label": fp,
                                            "slot_label": _index_label(anchor_idx - k)})
        if k in slots:
            info["duplicate_slot"].append(qed)
        slots[k] = (fp, val)
    kmax = max(slots)
    values, periods = [], []
    for k in range(kmax, -1, -1):
        if k in slots:
            fp, val = slots[k]
            periods.append(fp)
            values.append(val)
        else:
            lab = _index_label(anchor_idx - k) if anchor_idx is not None else "missing"
            periods.append(lab)
            values.append(None)
            info["missing_labels"].append(lab)
    return values, periods, info


def _load_series(raw, scan_quarter=None):
    meta_by_code = {}
    for m in raw.get("lineitems", []):
        code = m.get("ltcode") or m.get("LTCODE")
        if code:
            meta_by_code[code] = m

    series, unparsed = {}, {}
    unreadable_rows = {}
    currencies = {}
    _seen_rows = set()
    for r in raw.get("actuals", []):
        code = r.get("ltcode") or r.get("LTCODE")
        if not code:
            continue
        _rk = _row_key(r)
        if _rk in _seen_rows:
            continue
        _seen_rows.add(_rk)
        if (r.get("data_source") or r.get("DATA_SOURCE") or "actual").lower() != "actual":
            continue
        qed = (r.get("quarter_end_date") or r.get("QUARTERENDDATE") or "").strip()
        fp = clean_period_label(r.get("fiscal_period") or r.get("FISCALPERIOD"))
        _rawv = r.get("value", r.get("VALUE"))
        val, is_pct = parse_value(_rawv)
        _cur = parse_currency(_rawv)
        if _cur:
            currencies.setdefault(code, set()).add(_cur)
        if not qed or val is None:
            unparsed.setdefault(code, {
                "ltcode": code,
                "name": (meta_by_code.get(code, {}) or {}).get("lineitem")
                        or r.get("lineitem") or r.get("LINEITEM") or code,
                "samples": [], "rows": 0})
            unparsed[code]["rows"] += 1
            if qed:
                unreadable_rows.setdefault(code, {})[fp or qed] = str(_rawv)[:24]
            if val is None and len(unparsed[code]["samples"]) < 3:
                unparsed[code]["samples"].append(
                    str(r.get("value", r.get("VALUE")))[:24])
            continue
        s = series.get(code)
        if s is None:
            s = Series(meta_by_code.get(code, {"ltcode": code,
                                               "lineitem": r.get("lineitem") or r.get("LINEITEM")}))
            series[code] = s
        s.points[qed] = (fp, val)
        if is_pct:
            s.pct_votes += 1
        elif re.search(r"[$\u00a3\u20ac\u00a5\u00a2]",
                       str(r.get("value", r.get("VALUE")) or "")):
            s.currency_votes += 1
        else:
            _mv = VALUE_PAT.match(str(r.get("value", r.get("VALUE")) or ""))
            if (_mv and _mv.group("cur")
                    and (MINOR_UNIT_PREFIX.match(_mv.group("cur") or "")
                         or _cur)):
                s.currency_votes += 1
        if is_multiple(r.get("value", r.get("VALUE"))):
            s.multiple_votes += 1
        src = (r.get("SOURCE_DERIVED") or r.get("source_derived") or "").strip()
        if src:
            s.sources[src] = s.sources.get(src, 0) + 1
        if s.uic is None:
            s.uic = (r.get("UIC") or r.get("uic") or "").strip() or None

    _annual = {}
    for r in raw.get("annual_actuals") or []:
        _c = r.get("LTCODE") or r.get("ltcode")
        _fp = clean_period_label(r.get("FISCALPERIOD") or r.get("fiscal_period")).upper()
        _m = re.fullmatch(r"(?:FY)?(\d{4})", _fp)
        _v = parse_value(r.get("VALUE", r.get("value")))[0]
        _src = str(r.get("SOURCE_DERIVED") or r.get("source_derived") or "").strip().lower()
        if _c and _m and _v is not None and _src and _src != "calculated":
            _annual.setdefault(_c, {})[_m.group(1)[2:]] = _v
    built = []
    for s in series.values():
        _cs = currencies.get(s.ltcode) or set()
        s.currency = next(iter(_cs)) if len(_cs) == 1 else None
        s.currency_conflict = len(_cs) > 1
        s.values, s.periods, s.grid = _quarter_grid(s.points)
        s.values = s.values[-LOOKBACK_QUARTERS:]
        s.periods = s.periods[-LOOKBACK_QUARTERS:]
        s.grid_gaps = [p_ for p_, v_ in zip(s.periods, s.values) if v_ is None
                       and p_ in s.grid.get("missing_labels", [])]
        s.unreadable_by_label = unreadable_rows.get(s.ltcode, {})
        _blank_implausible_zeros(s)
        s.annual = _annual.get(s.ltcode, {})
        s.classify()
        s.derive()
        s._staleness(scan_quarter)
        built.append(s)

    seen, out, aliases = {}, [], []
    for s in sorted(built, key=lambda x: (_canonical_twin_score(x), x.category != "IS",
                                          x.ltcode)):
        key = tuple(round(v, 6) if v is not None else None for v in s.values)
        if len(s.values) >= 4 and key in seen:
            _w = seen[key]
            aliases.append({"ltcode": s.ltcode, "name": s.name,
                            "duplicate_of": _w.ltcode, "reason": "identical history",
                            "provenance": s.provenance,
                            "twin_provenance": _w.provenance})
            continue
        seen[key] = s
        out.append(s)

    out, near = _dedupe_near_identical(out)
    aliases += near
    live = {x.ltcode for x in out} | {a["ltcode"] for a in aliases}
    unreadable = [v for k, v in sorted(unparsed.items()) if k not in live]
    return out, aliases, unreadable


def _collapse_twins(series):
    groups = {}
    for x in series:
        groups.setdefault(concept_key(x.name), []).append(x)
    for _k, g in groups.items():
        adj = [x for x in g if "adjusted" in (x.tags or "").lower()]
        rep = [x for x in g if x not in adj]
        if not adj or not rep:
            continue

        def healthy(x):
            return (x.obs >= MIN_Q_FOR_RANK and not getattr(x, "stale", False)
                    and bool(x.values) and x.values[-1] is not None)
        a = sorted(adj, key=lambda x: x.ltcode)[0]
        r = sorted(rep, key=lambda x: x.ltcode)[0]
        if healthy(a):
            r.twin_collapsed_by, r.twin_reason = a.ltcode, "reported_twin"
            r.twin_collapsed_name = a.name
            a.basis_used = "adjusted"
        else:
            a.twin_collapsed_by, a.twin_reason = r.ltcode, "adjusted_twin_thin"
            a.twin_collapsed_name = r.name
            r.basis_used = ("reported: the adjusted twin %s has %d quarters%s"
                            % (a.ltcode, a.obs, ", not the scan quarter"
                               if getattr(a, "stale", False) else ""))


NEAR_DUP_TOL = 0.0015
NEAR_DUP_MIN_OVERLAP = 8


def _canonical_twin_score(s):
    return (
        0 if (s.provenance or "") == "filed" else 1,
        0 if s.polarity != 0 else 1,
        s.tier if isinstance(s.tier, int) else 9,
        -len([v for v in s.values if v is not None]),
        len(s.name or ""),
        s.ltcode or "",
    )


def _dedupe_near_identical(built):
    canonical_score = _canonical_twin_score

    keep, dropped = [], []
    for s in built:
        twin = None
        for k in keep:
            if k.kind != s.kind or k.unit != s.unit:
                continue
            pairs = [(a, b) for a, b in zip(k.values, s.values)
                     if a is not None and b is not None]
            if len(pairs) < NEAR_DUP_MIN_OVERLAP or len(pairs) < min(
                    len(k.values), len(s.values)):
                continue
            if all(abs(a - b) <= NEAR_DUP_TOL * max(abs(a), abs(b), 1e-9)
                   for a, b in pairs):
                twin = k
                break
        if twin is None:
            keep.append(s)
            continue
        win, lose = sorted((twin, s), key=canonical_score)[0], None
        lose = s if win is twin else twin
        if win is not twin:
            keep[keep.index(twin)] = s
        dropped.append({
            "ltcode": lose.ltcode, "name": lose.name,
            "duplicate_of": win.ltcode,
            "provenance": lose.provenance, "twin_provenance": win.provenance,
            "reason": "same series as " + (win.display or win.name) +
                      " to within a tenth of a percent on every shared quarter",
        })
    return keep, dropped


SHARE_PREFIX_PAT = re.compile(r"^\s*%\s*(revenue|total revenue|sales)\s*-\s*", re.I)
PER_SHARE_PAT = re.compile(r"\s*/\s*share\s*$|\s+per share\s*$", re.I)


def _suppress_share_twins(cands):
    by_concept = {}
    for s in cands:
        name = (s.name or "").strip()
        m = SHARE_PREFIX_PAT.match(name)
        if m:
            by_concept.setdefault(name[m.end():].strip().lower(), {})["share"] = s
        elif PER_SHARE_PAT.search(name):
            _slot = by_concept.setdefault(PER_SHARE_PAT.sub("", name).strip().lower(), {})
            _slot["share"] = s
            _slot["per_share"] = True
        else:
            by_concept.setdefault(name.lower(), {})["level"] = s
    suppressed = {}
    for _, pair in by_concept.items():
        lv, sh = pair.get("level"), pair.get("share")
        if lv is None or sh is None:
            continue
        if (lv.z > 0) != (sh.z > 0):
            continue
        if pair.get("per_share") and sh.tier == 1 and lv.tier == 1:
            loser, winner = lv, sh
        else:
            loser = sh if abs(lv.z) >= abs(sh.z) else lv
            winner = lv if loser is sh else sh
        suppressed[loser.ltcode] = winner.ltcode
    ratios = [x for x in cands if x.kind == "ratio" and x.ltcode not in suppressed]
    for i, a in enumerate(ratios):
        for b in ratios[i + 1:]:
            if a.ltcode in suppressed or b.ltcode in suppressed:
                continue
            pairs = [(u, v) for u, v in zip(a.values or [], b.values or [])
                     if u is not None and v is not None]
            if len(pairs) < 8:
                continue
            tot = [u + v for u, v in pairs]
            for full in (100.0, 1.0):
                _dev = sorted(abs(t - full) for t in tot)
                if (_dev[-1] <= 0.005 * full
                        and _dev[-2] <= 0.003 * full
                        and _dev[len(_dev) // 2] <= 0.001 * full):
                    pa = polarity_of(a.name, a.ltcode, getattr(a, "template", ANY_TEMPLATE))
                    pb = polarity_of(b.name, b.ltcode, getattr(b, "template", ANY_TEMPLATE))
                    keep_a = ((pa > 0, a.provenance == "filed", -len(a.ltcode or ""),
                               a.ltcode or "")
                              >= (pb > 0, b.provenance == "filed", -len(b.ltcode or ""),
                                  b.ltcode or ""))
                    loser, winner = (b, a) if keep_a else (a, b)
                    suppressed[loser.ltcode] = winner.ltcode
                    if not (pa and pb and pa == -pb):
                        winner._mix_share = True
                    break
    return suppressed


GENERIC_BS_PAT = re.compile(
    r"accounts payable|accrued|current liabilit|current assets|total liabilit|"
    r"total assets|other (current |non-?current )?(assets|liabilit)|operating lease|"
    r"common stock|comprehensive|minority interest|non-?controlling|"
    r"property,? plant|\bpp&e\b|total equity|shareholders.? equity|"
    r"liabilities and equity|preferred stock|intangible|goodwill|"
    r"deferred (income )?tax|right-of-use|held for sale|long[- ]?term assets|"
    r"long[- ]?term debt|short[- ]?term debt|total debt|net debt|"
    r"raw materials|work[- ]in[- ]process|finished goods|\(balance sheet\)|\s-\s*bs\b|"
    r"notes payable|loans and notes", re.I)
TABLE_MAX_MARGINS = 2
GEO_CUT_PAT = re.compile(r"\b(asia|europe|latin america|north america|pacific|emea|canada|japan|"
                         r"china|middle east|africa|cemea|lac|ap\b)", re.I)
EBITDA_OFF_TEMPLATE_PENALTY = 0.15
READ_BALANCE_PAT = re.compile(
    r"receivab|inventor|deferred revenue|contract liabilit|customer advance|deposit|\bloans?\b|"
    r"assets under management|\baum\b|premium|reserve|book value|backlog|rpo|"
    r"remaining performance", re.I)
SEGMENT_PAREN_PAT = re.compile(r"\((?!ex|excl|incl|adj|consolidated)[^)]*\)\s*$", re.I)


def _margin_stem(name):
    n = SEGMENT_PAREN_PAT.sub("", name or "")
    n = re.sub(r"\s*-\s*consolidated\b|\(consolidated\)", "", n, flags=re.I)
    return re.sub(r"\s+", " ", n).strip().lower()


def _basis_stem(name):
    m = BASIS_SUFFIX_PAT.search(name or "")
    return re.sub(r"^%\s*", "", (name or "")[:m.start()]).strip().lower() if m else None


def _table_ok(s, allow_rate=False):
    status = s.unit_info[3]
    if status != "validated":
        return False
    if (GENERIC_BS_PAT.search(s.name or "") or GENERIC_BS_PAT.search(s.display or "")) \
            and template_rank(s.template, s.name) is None:
        return False
    if str(s.ltcode or "").startswith("BS_") and template_rank(s.template, s.name) is None \
            and not READ_BALANCE_PAT.search(s.name or ""):
        return False
    if s.template == "reit" and s.kind == "ratio" and s.provenance == "derived":
        return False
    if SCHEDULE_BUCKET_PAT.search((s.name or "").strip()):
        return False
    if re.search(r"segment assets|\bassets\s*-\s*segment", s.name or "", re.I):
        return False
    if getattr(s, "segment_cost", False):
        return False
    if s.kind == "level" and re.search(r"constant currency revenue|revenue.*constant currency|"
                                        r"\bcc revenue\b", s.name or "", re.I):
        return False
    if re.search(r"(purchases?|sales?|maturit\w*) of (investments|securities|marketable)|"
                 r"investing activit", s.name or "", re.I):
        return False
    if re.search(r"^(total\s+)?others?\b(?!\s*(income|operating|comprehensive))|\s-\s*others?\s*$|"
                 r"\s-\s*others?\s+revenue\s*$", (s.name or "").strip(), re.I):
        return False
    if re.search(r"share repurchase|repurchase of|buy-?backs?|^dividends?(\s*paid)?\s*$|"
                 r"dividends? paid", s.name or "", re.I):
        return False
    if re.search(r"^average (debt|equity|invested|capital|total assets)|debt\s*&\s*equity|"
                 r"invested capital|\b\d\s*year stack\b|^%?\s*spread\s*-", s.name or "", re.I) \
            and template_rank(getattr(s, "template", None), s.name) is None:
        return False
    if re.search(r"\s-\s*adj(usted)?\.?\s*(net income|ebitda|ebit|eps|operating income|earnings)\s*$",
                 s.name or "", re.I):
        return False
    if s.kind == "ratio" and s.provenance == "derived" and re.search(r"\betr\b|tax rate", s.name or "", re.I):
        return False
    if re.search(r"financing activit|\bcff\b|-\s*cff\s*$", s.name or "", re.I):
        return False
    if getattr(s, "spliced_with", None) or getattr(s, "shadowed_by", None):
        return False
    if s.kind == "level" and s.provenance == "derived" \
            and template_rank(getattr(s, "template", None), s.name) is None \
            and not s.headline_tier \
            and not SPINE_PAT.search(s.name or "") and not SECTOR_KPI_PAT.search(s.name or ""):
        return False
    if s._is_cash_flow() and s._is_flow() and s.period_basis()[0] != "quarterly":
        return False
    return True


def _small_expense(s, rev_value):
    if template_rank(getattr(s, "template", None), s.name) is not None:
        return False
    _nm = s.name or ""
    _capex = re.search(r"\bcapex\b|capital expend", _nm, re.I)
    if s.kind == "ratio" and s.latest_value is not None \
            and re.search(r"^%\s*revenue\s*-|as % of revenue|%\s*revenue\s*-", _nm, re.I):
        _ex = re.search(r"amortiz|depreciat|share-?based|stock-?based|\bsbc\b|compensation|"
                        r"restructur|impairment|litigation|severance", _nm, re.I)
        try:
            return bool(_capex and abs(s.latest_value) < 3.0) or bool(_ex and abs(s.latest_value) < 1.0)
        except Exception:
            return False
    if s.kind != "level" or not rev_value or s.latest_value is None:
        return False
    if _capex:
        try:
            return abs(s.latest_value) < 0.03 * abs(rev_value)
        except Exception:
            return False
    if not re.search(r"amortiz|depreciat|share-?based|stock-?based|\bsbc\b|compensation|\binterest\b|"
                     r"\btax\b|other (income|expense)|restructur|impairment|acquisition|"
                     r"litigation|severance|variable costs?|fixed costs?|\bcosts?\b|expense",
                     s.name or "", re.I):
        return False
    try:
        return abs(s.latest_value) < 0.01 * abs(rev_value)
    except Exception:
        return False


SCHEDULE_BUCKET_PAT = re.compile(
    r"^(19|20)\d\d\b|^there\s*after|^therafter|^beginning\b|^ending\b|"
    r"^total\s*-\s*(net effective rent|occupied sq)", re.I)


def _same_history(a, b):
    if a.kind != b.kind or a.kind not in ("level", "ratio"):
        return False
    if a.kind == "ratio":
        pa, pb = (a.values or [])[-8:], (b.values or [])[-8:]
        pairs = [(x, y) for x, y in zip(pa, pb) if x is not None and y is not None]
        return len(pairs) >= 6 and all(abs(x - y) <= 0.01 * max(1.0, abs(y)) for x, y in pairs)
    va = [v for v in (a.values or [])[-4:]]
    vb = [v for v in (b.values or [])[-4:]]
    if len(va) < 4 or len(vb) < 4 or None in va or None in vb:
        return False
    if any(not x or abs(x - y) > 0.02 * abs(x) for x, y in zip(va, vb)):
        return False
    ga = [g for g in (a.growth or [])[-6:]]
    gb = [g for g in (b.growth or [])[-6:]]
    if len(ga) < 6 or len(gb) < 6:
        return False
    return all(x is not None and y is not None and abs(x - y) <= 1.0 for x, y in zip(ga, gb))


def _rank(series, rev_value=None):
    rankable = [s for s in series if s.rankable]
    material = [s for s in rankable if s.material_inflection]
    context = [s for s in rankable if s.primary_context]
    _by_stem = {}
    for x in rankable:
        if PER_SHARE_PAT.search(x.name or "") and x.tier == 1 and x.primary_context \
                and template_rank(x.template, x.name) == 0:
            _by_stem[PER_SHARE_PAT.sub("", x.name).strip().lower()] = x
    if _by_stem:
        _ctx2 = []
        for x in context:
            sh = _by_stem.get((x.name or "").strip().lower())
            if sh is not None and sh is not x and sh not in _ctx2:
                _ctx2.append(sh)
            elif x not in _ctx2 and (x.name or "").strip().lower() not in (
                    PER_SHARE_PAT.sub("", y.name).strip().lower() for y in _ctx2):
                _ctx2.append(x)
        context = _ctx2
    suppressed = _suppress_share_twins(material)
    keep = [s for s in material if s.ltcode not in suppressed]
    def order(rows):
        return sorted(rows, key=lambda s: (-s.priority(rev_value)[0], -abs(s.z),
                                           s.ltcode or ""))

    def drop_near_twins(rows):
        out = []
        for s in rows:
            _bs = _basis_stem(s.name)
            if _bs:
                _tw = next((o for o in out if _basis_stem(o.name) == _bs), None)
                if _tw is not None:
                    if "top_kpi" in (s.tags or "") and "top_kpi" not in (_tw.tags or ""):
                        out[out.index(_tw)] = s
                    continue
            if any(o.kind == s.kind and abs((o.z or 0) - (s.z or 0)) < 0.15
                   and abs((o.latest_growth or 0) - (s.latest_growth or 0)) < 1.0
                   and _margin_stem(o.name) == _margin_stem(s.name)
                   for o in out):
                continue
            _stem_adj = lambda n: re.sub(r"\s*(-\s*adjusted|\(ex-?\s?1x items?\)|\badjusted\b)", "",
                                         _margin_stem(n), flags=re.I).strip()
            if s.kind == "ratio" and any(
                    o.kind == "ratio" and _stem_adj(o.name) == _stem_adj(s.name) and o.name != s.name
                    and abs((o.latest_growth or 0) - (s.latest_growth or 0)) <= 10.0
                    and abs((o.latest_accel or 0) - (s.latest_accel or 0)) <= 10.0
                    for o in out):
                continue
            _tw2 = next((o for o in out if _same_history(o, s)), None)
            if _tw2 is not None:
                _good = re.compile(r"rate|ratio|%|margin", re.I)
                if s.kind == "ratio" and _good.search(s.name or "") and not _good.search(_tw2.name or ""):
                    out[out.index(_tw2)] = s
                continue
            out.append(s)
        return order(out)

    def cap_margins(rows):
        out, n = [], 0
        cons = {_margin_stem(s.name) for s in rows
                if s.kind == "ratio" and not SEGMENT_PAREN_PAT.search(s.name or "")}
        for s in rows:
            if s.kind == "ratio" and (re.search(r"margin|^gm\b", s.name or "", re.I)
                                      or re.search(r"margin", s.display or "", re.I)):
                if SEGMENT_PAREN_PAT.search(s.name or "") and _margin_stem(s.name) in cons:
                    continue
                if n >= TABLE_MAX_MARGINS:
                    continue
                n += 1
            out.append(s)
        return out

    def cap_families(rows):
        out, used = [], {}
        for s in rows:
            fam = _family(s.name, s)
            _own = (template_rank(s.template, s.name) is not None
                    and not SEGMENT_DETAIL_PAT.search(s.name or "")
                    and not GEO_CUT_PAT.search(s.name or ""))
            if used.get(fam, 0) >= MAX_PER_FAMILY and not _own:
                continue
            if (fam == "earnings" and not _own and _NET_MARGIN_PAT.search(s.name or "")
                    and any(_EPS_NAME_PAT.search(o.name or "")
                            and bool(_ADJ_BASIS_PAT.search(o.name or "")) == bool(_ADJ_BASIS_PAT.search(s.name or ""))
                            for o in out)):
                continue
            used[fam] = used.get(fam, 0) + 1
            out.append(s)
        return out

    keep = [s for s in keep if _table_ok(s) and not _small_expense(s, rev_value)]
    rates = [s for s in rankable if s.kind == "pre_growth" and _table_ok(s, allow_rate=True)
             and (s.material_inflection or s.primary_context
                  or template_rank(s.template, s.name) is not None)]
    keep = [s for s in keep if s.kind != "pre_growth"]
    accel = cap_margins(drop_near_twins(cap_families(order([s for s in keep if s.z > 0 and s.latest_accel > 0]))))
    decel = cap_margins(drop_near_twins(cap_families(order([s for s in keep if s.z < 0 and s.latest_accel < 0]))))
    def _srank(s):
        if (s.segment_revenue_share(rev_value) or 0) >= SEGMENT_SLOT_SHARE:
            _slot = _template_revenue_slot(s.template)
            return _slot if _slot is not None else 99
        if getattr(s, "segment_scoped", False) or (s.ltcode or "").startswith("SD_") \
                or SEGMENT_DETAIL_PAT.search(s.name or ""):
            return 99
        tr = template_rank(s.template, s.name)
        return tr if tr is not None else 99
    def order_ctx(rows):
        return sorted(rows, key=lambda s: (
            1 if _ebitda_off_template({"template": s.template, "provenance": s.provenance,
                                       "name": s.name}) else 0,
            _srank(s), -s.priority(rev_value)[0], -abs(s.z), s.ltcode or ""))
    ctx = cap_families(order_ctx([s for s in context if s.kind != "pre_growth"
                                  and _table_ok(s)]))[:CONTEXT_N]
    rates = cap_families(sorted(rates, key=lambda s: (_srank(s), 0 if "top_kpi" in (s.tags or "") else 1,
                                                      0 if s.material_inflection else 1,
                                                      -abs(s.z), s.ltcode or "")))[:TOP_N]
    return accel, decel, rankable, suppressed, ctx, rates


def _find_eps(series):
    return (_find(series, "IS_P_128", "IS_P_127")
            or _find(series, name_pat=r"^adjusted (diluted )?eps$|^diluted (eps|net (income|loss)[\w ()]* per share)$")
            or _find(series, name_pat=r"\beps\b|earnings per share"))


def _find(series, *codes, name_pat=None):
    for c in codes:
        for s in series:
            if s.ltcode == c and s.rankable:
                return s
    if name_pat:
        for s in series:
            if s.rankable and re.search(name_pat, s.name, re.I):
                return s
    return None


RULE_OWNER = {
    "load_factor_versus_unit_revenue": "airlines",
    "fuel_outpacing_traffic": ("airlines", "industrials"),
    "volume_versus_price_per_load": "industrials",
    "revenue_versus_operating_ratio": "industrials",
    "revenue_up_operating_ratio_up": "industrials",
    "rate_base_versus_revenue": "utilities",
    "volumes_versus_revenue": "utilities",
    "volumes_versus_margin": "utilities",
    "throughput_versus_ebitda": "midstream",
    "ebitda_versus_coverage": "midstream",
    "revenue_versus_dcf": "midstream",
    "flows_versus_aum": "asset_mgmt",
    "base_fee_versus_aum": "asset_mgmt",
    "loss_ratio_versus_membership": "healthcare",
    "revenue_versus_loss_ratio": ("insurance", "healthcare"),
    "production_versus_realisation": "e_and_p",
    "realisation_versus_cost": "e_and_p",
    "loans_up_spread_income_down": "banks",
    "balance_growth_at_lower_margin": "banks",
    "provision_ahead_of_losses": "banks",
    "losses_ahead_of_provision": "banks",
    "loans_outpacing_deposits": "banks",
}

RULE_UNIVERSAL = frozenset({
    "revenue_up_margin_down", "revenue_down_margin_up", "revenue_up_opmargin_down",
    "eps_outpacing_revenue", "cash_diverging_from_revenue",
    "working_capital_outgrowing_revenue", "revenue_up_ebitda_down",
    "ebitda_up_revenue_down", "revenue_up_cash_down", "cash_up_revenue_down",
    "revenue_outrunning_cash", "revenue_versus_cash", "segment_divergence",
    "counter_polarity", "volume_versus_price", "price_and_unit_cost_both_rising",
})


def _rule_scope(rule):
    own = RULE_OWNER.get(rule)
    if own is not None:
        return list(own) if isinstance(own, tuple) else [own]
    if rule in RULE_UNIVERSAL:
        return ["*"]
    raise SystemExit(
        "divergence rule %r is in neither RULE_OWNER nor RULE_UNIVERSAL. Declare "
        "which templates own it, or declare it universal - an undeclared rule "
        "publishes with no owner and no gate can tell that from a mistake." % rule)


COMMENTARY_FIELDS = ("text", "speaker", "role", "quarter", "document", "document_date",
                     "document_id", "attributed_to_metric")


def _load_commentary(path):
    if not path:
        return {}
    try:
        with open(path, encoding="utf-8") as fh:
            blob = json.load(fh)
    except (OSError, ValueError) as e:
        _fail("build", "--commentary %s could not be read: %s" % (path, e))
    if not isinstance(blob, dict):
        _fail("build", "--commentary %s must be a JSON object mapping LTCODE to an "
                       "attribution object; got %s" % (path, type(blob).__name__))
    bad = sorted(k for k, v in blob.items() if not str(k).startswith("_")
                 and not isinstance(v, (dict, list)))
    if bad:
        _fail("build", "--commentary %s: the value for %s is not an attribution object "
                       "(each needs %s)" % (path, ", ".join(bad[:5]), ", ".join(COMMENTARY_FIELDS)))
    return blob


COMMENTARY_STANDING = ("explained", "partly explained", "not addressed",
                       "conflicting", "unavailable")


def _commentary_standing(s, context=None):
    ctx = (context or {}).get("commentary") or {}
    if not ctx:
        return {"standing": "unavailable",
                "basis": "no management commentary was supplied to this run"}
    q = ctx.get(s.ltcode) or ctx.get(s.name)
    if not q:
        return {"standing": "not addressed",
                "basis": "commentary was retrieved for this note and does not "
                         "address this line"}
    txt = str(q.get("text") or "")
    up = re.search(r"\b(grew|growth|increase\w*|higher|improv\w+|expand\w+|"
                   r"stronger|accelerat\w+)\b", txt, re.I)
    down = re.search(r"\b(declin\w+|decreas\w+|lower|weaker|fell|contract\w+|"
                     r"pressur\w+|slow\w+)\b", txt, re.I)
    moved_up = (s.latest_growth or 0) > 0
    if up and down:
        return {"standing": "partly explained",
                "basis": "the commentary names both directions for this line"}
    if (up and not moved_up) or (down and moved_up):
        return {"standing": "conflicting",
                "basis": "the commentary's direction for this line and the "
                         "reported direction disagree"}
    if up or down:
        return {"standing": "explained",
                "basis": "the commentary addresses this line in the direction "
                         "reported"}
    return {"standing": "partly explained",
            "basis": "the commentary mentions this line without a direction"}


def _confirmations(series):
    rev = _find(series, "IS_P_101", name_pat=r"total revenue|^revenue$|net sales")
    om = _find(series, "IS_P_110-M", "IS_P_109-M", name_pat=r"(operating|ebit) margin")
    eps = _find_eps(series)
    ocf = _find(series, "CF_P_108", name_pat=r"^\s*ocf\s*$|operating cash flow")
    fcf = _find(series, "CF_P_110", name_pat=r"^\s*fcf\s*$|free cash flow")
    out = []
    if not (rev and rev.z is not None):
        return out

    def _axis(s):
        if not (s and s.rankable and s.latest_accel is not None):
            return None
        if s.material_inflection:
            return "statistical and economic"
        if s.large_move:
            return "economic alone"
        return None

    def pair(rule, other, what):
        a_rev, a_oth = _axis(rev), _axis(other)
        if not (a_rev and a_oth):
            return
        if (rev.latest_accel > 0) != (other.latest_accel > 0):
            return
        way = ("accelerating together" if rev.latest_accel > 0
               else "decelerating together")
        _hl = rev.display + " and " + other.display + " are " + way
        _gr, _go = rev.latest_growth, other.latest_growth
        _split = _gr is not None and _go is not None and (_gr < 0) != (_go < 0)
        if _split:
            _dn, _up = (rev, other) if _gr < 0 else (other, rev)
            _hl = (_dn.display + " fell y/y while " + _up.display + " still grew; both "
                   + ("sped up" if rev.latest_accel > 0 else "slowed") + " from last quarter")
        out.append({
            "rule": rule,
            "headline": _hl,
            "split_levels": _split,
            "relationship": what,
            "basis": "observed relationship",
            "qualified_on": {rev.display: a_rev, other.display: a_oth},
            "owner_template": None,
            "metrics": [{"ltcode": m.ltcode, "name": m.name, "display": m.display,
                         "z": _clamp_display(m.z), "accel": m.latest_accel,
                         "growth": m.latest_growth,
                         "unit": m.unit} for m in (rev, other)],
        })

    pair("revenue_and_operating_margin_together", om,
         "The margin is earned on this revenue")
    pair("revenue_and_eps_together", eps,
         None)
    pair("revenue_and_cash_together", fcf or ocf,
         None)
    return out


def _together_question(legs, story):
    a, b = legs[0], legs[1]
    ga, gb = a.get("growth"), b.get("growth")
    if ga is not None and gb is not None and (ga < 0) != (gb < 0):
        dn, up = (a, b) if ga < 0 else (b, a)
        return "Does %s keep growing if %s stays lower y/y?" % (up["display"], dn["display"])
    acc = [m.get("accel") for m in legs if m.get("accel") is not None]
    if acc and all(x > 0 for x in acc):
        return "Do %s and %s keep speeding up together?" % (a["display"], b["display"])
    return "Do %s and %s keep slowing together?" % (a["display"], b["display"])


def _eps_bridge(series, eps, rev):
    adj = bool(re.search(r"ex-?\s?1x|adj", eps.name or "", re.I))
    ebit = _find(series, *(("IS_P_110", "IS_P_109") if adj else ("IS_P_109", "IS_P_110")))
    ni = _find(series, *(("IS_P_124", "IS_P_123") if adj else ("IS_P_123", "IS_P_124")))
    fds = _find(series, "IS_P_126", "IS_P_125")
    om = _find(series, *(("IS_P_110-M", "IS_P_109-M") if adj else ("IS_P_109-M", "IS_P_110-M")))
    if ebit is None or ebit.latest_growth is None or rev.latest_growth is None \
            or eps.latest_growth is None:
        return None
    if ni is not None and ni.latest_growth is None:
        ni = None
    steps = [{"label": rev.display, "growth": rev.latest_growth},
             {"label": ebit.display, "growth": ebit.latest_growth,
              "margin_change_bps": (om.latest_growth if om is not None else None),
              "margin_display": (om.display if om is not None else None)},
             ] + ([{"label": ni.display, "growth": ni.latest_growth}] if ni is not None else [])
    if fds is not None and fds.latest_growth is not None:
        steps.append({"label": fds.display, "growth": fds.latest_growth})
    steps.append({"label": eps.display, "growth": eps.latest_growth})
    txt = "%s %s against %s %s: %s %s" % (
        eps.display, _fmt_growth(eps.latest_growth, "pct"), rev.display,
        _fmt_growth(rev.latest_growth, "pct"), ebit.display,
        _fmt_growth(ebit.latest_growth, "pct"))
    if om is not None and om.latest_growth is not None:
        txt += " as the margin moved %s" % _fmt_accel(om.latest_growth, "bps")
    if ni is not None:
        below = ("added to" if ni.latest_growth > ebit.latest_growth else "took from")
        txt += "; %s %s, so items below operating profit %s the growth" % (
            ni.display, _fmt_growth(ni.latest_growth, "pct"), below)
    if fds is not None and fds.latest_growth is not None:
        txt += "; %s %s y/y" % (fds.display, _fmt_growth(fds.latest_growth, "pct"))
    if ni is None:
        txt += ("; the rest of the gap is below operating profit (interest, other income and tax)"
                if fds is not None else "; the rest of the gap is below operating profit and in the share count")
    return {"steps": steps, "text": txt + "."}


def _divergences(series, subindustry=None, template=None):
    rev = _find(series, "IS_P_101", name_pat=r"total revenue|^revenue$|net sales")
    gm = _find(series, "IS_P_106-M", "IS_P_105-M", name_pat=r"gross margin")
    om = _find(series, "IS_P_110-M", "IS_P_109-M", name_pat=r"(operating|ebit) margin")
    eps = _find_eps(series)
    ocf = _find(series, "CF_P_108", name_pat=r"^\s*ocf\s*$|operating cash flow")
    fcf = _find(series, "CF_P_110", name_pat=r"^\s*fcf\s*$|free cash flow")

    out = []

    def add(rule, headline, metrics):
        ms = [m for m in metrics if m is not None]
        def _pace(m):
            a = m.latest_accel
            verb = ("accelerated" if a > 0 else "slowed") if a else "held its pace"
            if m.kind == "ratio":
                g, pg = m.latest_growth, m.prev_growth
                if g is None or pg is None or not a:
                    return "the year-on-year change in " + m.display + " " + verb
                if g >= 0 and pg < 0:
                    return m.display + " turned from contraction to expansion"
                if g < 0 and pg >= 0:
                    return m.display + " turned from expansion to contraction"
                if g >= 0:
                    return m.display + (" expanded faster" if a > 0 else " expanded more slowly")
                return m.display + (" contracted less" if a > 0 else " contracted more")
            g, pg = m.latest_growth, m.prev_growth
            if g is not None and pg is not None and a:
                if g < 0 and pg < 0:
                    return m.display + ("'s decline eased" if a > 0 else "'s decline deepened")
                if g < 0 <= pg:
                    return m.display + " turned from growth to decline"
                if pg < 0 <= g:
                    return m.display + " turned from decline to growth"
            return m.display + " growth " + verb
        if (len(ms) == 2 and ("opposite directions" in headline
                              or re.search(r"(?:ac|de)celerat\w* while .* (?:ac|de)celerat",
                                           headline))
                and all(m.latest_accel is not None for m in ms)):
            lead, lag = sorted(ms, key=lambda m: -(m.latest_accel or 0))
            h = _pace(lead) + " while " + _pace(lag)
            headline = h[:1].upper() + h[1:]
        elif len(ms) == 2 and "opposite directions" in headline:
            headline = (ms[0].display + " and " + ms[1].display +
                        " are moving in opposite directions")
        elif len(ms) == 2 and "both accelerating" in headline:
            headline = ms[0].display + " and " + ms[1].display + " are both accelerating"
        out.append({"rule": rule, "headline": headline,
                    "owner_template": _rule_scope(rule),
                    "metrics": [{"ltcode": m.ltcode, "name": m.name,
                                 "display": m.display,
                                 "z": _clamp_display(m.z),
                                 "accel": m.latest_accel, "unit": m.unit} for m in metrics]})

    if rev and gm and rev.z > MATERIAL_Z and gm.z < -MATERIAL_Z:
        add("revenue_up_margin_down",
            "Revenue is accelerating while gross margin decelerates", [rev, gm])
    if rev and gm and rev.z < -MATERIAL_Z and gm.z > MATERIAL_Z:
        add("revenue_down_margin_up",
            "Revenue is decelerating while gross margin accelerates", [rev, gm])
    if rev and om and rev.z > MATERIAL_Z and om.z < -MATERIAL_Z:
        add("revenue_up_opmargin_down",
            "Revenue is accelerating while operating margin decelerates", [rev, om])
    if rev and eps and eps.z is not None and rev.z is not None and (eps.z - rev.z) > STRONG_Z \
            and eps.latest_growth is not None and rev.latest_growth is not None \
            and eps.latest_growth > rev.latest_growth:
        add("eps_outpacing_revenue",
            (eps.display + " is growing faster than revenue"), [eps, rev])
        out[-1]["bridge"] = _eps_bridge(series, eps, rev)
        out[-1]["bridge_available"] = out[-1]["bridge"] is not None
    ocf = ocf if (ocf is not None and ocf.rankable) else None
    fcf = fcf if (fcf is not None and fcf.rankable) else None
    if rev and (ocf or fcf):
        cash = fcf or ocf
        if rev.z > MATERIAL_Z and cash.z < -MATERIAL_Z:
            add("cash_diverging_from_revenue",
                "Revenue is accelerating while cash generation decelerates", [rev, cash])
    is_financial = bool(FINANCIALS_PAT.search(subindustry or ""))
    if rev and rev.latest_growth is not None and rev.latest_value and not is_financial:
        for s in series:
            big_enough = (s.latest_value is not None
                          and abs(s.latest_value) >= 0.10 * abs(rev.latest_value))
            if (s.rankable and WORKING_CAPITAL_PAT.search(s.name)
                    and not NOT_WORKING_CAPITAL_PAT.search(s.name)
                    and template not in NO_WORKING_CAPITAL_SECTORS
                    and s.kind == "level" and s.latest_growth is not None
                    and big_enough
                    and s.latest_growth - rev.latest_growth > 10.0):
                add("working_capital_outgrowing_revenue",
                    "A working capital line is growing materially faster than revenue",
                    [s, rev])
                _cash = ocf if (ocf is not None and ocf.latest_growth is not None
                                and ocf.period_basis()[0] == "quarterly") else None
                if _cash is not None:
                    out[-1]["cash_context"] = {
                        "text": "%s %s y/y against revenue %s; %s %s y/y to %s over the same quarter."
                                % (s.display, _fmt_growth(s.latest_growth, "pct"),
                                   _fmt_growth(rev.latest_growth, "pct"), _cash.display,
                                   _fmt_growth(_cash.latest_growth, "pct"),
                                   _money(_cash.latest_value, getattr(_cash, "currency", None))),
                        "ocf_ltcode": _cash.ltcode}
                break

    if is_financial:
        nii = _find(series, name_pat=r"net interest income|^\s*nii\s*$")
        nim = _find(series, name_pat=r"net interest margin|^\s*nim\s*$")
        loans = _find(series, name_pat=r"^total loans|\bloans\b")
        deposits = _find(series, name_pat=r"\bdeposits?\b")
        prov = _find(series, name_pat=r"provision")
        nco = _find(series, name_pat=r"charge-?off|\bnco\b")
        if nii and loans and loans.z > MATERIAL_Z and nii.z < -MATERIAL_Z:
            add("loans_up_spread_income_down",
                "Loan balances are accelerating while spread income decelerates",
                [loans, nii])
        if nim and loans and nim.z < -MATERIAL_Z and loans.z > MATERIAL_Z:
            add("balance_growth_at_lower_margin",
                "Balance growth is accelerating as margin compresses", [loans, nim])
        if prov and nco and prov.z > MATERIAL_Z and nco.z < -MATERIAL_Z:
            add("provision_ahead_of_losses",
                "Provisioning is accelerating while realised losses decelerate",
                [prov, nco])
        if prov and nco and prov.z < -MATERIAL_Z and nco.z > MATERIAL_Z:
            add("losses_ahead_of_provision",
                "Realised losses are accelerating while provisioning decelerates",
                [nco, prov])
        if loans and deposits and loans.z > MATERIAL_Z and deposits.z < -MATERIAL_Z:
            add("loans_outpacing_deposits",
                "Loan growth is accelerating while deposit growth decelerates",
                [loans, deposits])

    def _eligible(x):
        return bool(x) and x.latest_accel is not None and (
            x.significant or x.large_move)

    def opposed(a, b, bar=MATERIAL_Z):
        if not (a and b) or a.latest_accel is None or b.latest_accel is None:
            return False
        if not (_eligible(a) and _eligible(b)):
            return False
        if (a.latest_accel > 0) == (b.latest_accel > 0):
            return False
        return max(abs(a.z or 0), abs(b.z or 0)) >= bar

    def counter_polarity(a, b, bar=MATERIAL_Z):
        if not (a and b) or a.latest_accel is None or b.latest_accel is None:
            return False
        if not (_eligible(a) and _eligible(b)):
            return False
        if a.polarity == 0 or b.polarity == 0 or a.polarity == b.polarity:
            return False
        if (a.latest_accel > 0) != (b.latest_accel > 0):
            return False
        return max(abs(a.z or 0), abs(b.z or 0)) >= bar

    def divergent(a, b):
        return opposed(a, b) or counter_polarity(a, b)

    ebitda = next((x for x in series
                   if x.rankable and x.kind == "level"
                   and re.search(r"adjusted ebitda|modified ebitda|\bebitda\b",
                                 x.name or "", re.I)
                   and not re.search(r"\bmargin\b", x.name or "", re.I)), None)
    dcf = _find(series, name_pat=r"distributable cash flow|\bdcf\b")
    cover = _find(series, name_pat=r"coverage ratio|distribution coverage")
    thru = _find(series, name_pat=r"throughput|gathering volumes")

    if opposed(rev, ebitda):
        add("revenue_up_ebitda_down" if rev.latest_accel > 0 else "ebitda_up_revenue_down",
            "Revenue and adjusted EBITDA are moving in opposite directions",
            [rev, ebitda])
    if opposed(rev, dcf):
        add("revenue_up_cash_down" if rev.latest_accel > 0 else "cash_up_revenue_down",
            "Revenue and distributable cash flow are moving in opposite directions",
            [rev, dcf])
    if opposed(ebitda, cover):
        add("ebitda_versus_coverage",
            "Adjusted EBITDA and distribution coverage are moving in opposite directions",
            [ebitda, cover])
    if opposed(thru, ebitda):
        add("throughput_versus_ebitda",
            "Throughput and adjusted EBITDA are moving in opposite directions",
            [thru, ebitda])

    rate_base = _find(series, name_pat=r"rate base")
    volumes = _find(series, name_pat=r"energy sales|retail\s*-?\s*sales|load growth")
    if opposed(rate_base, rev):
        add("rate_base_versus_revenue",
            "Rate base and revenue are moving in opposite directions", [rate_base, rev])
    if opposed(volumes, rev):
        add("volumes_versus_revenue",
            "Sales volumes and revenue are moving in opposite directions", [volumes, rev])
    if opposed(volumes, om):
        add("volumes_versus_margin",
            "Sales volumes and operating margin are moving in opposite directions",
            [volumes, om])

    prod = _find(series, name_pat=r"average daily production|^total production")
    realise = _find(series, name_pat=r"wellhead revenue|realized price|revenue per boe")
    unitcost = _find(series, name_pat=r"cost\s*/\s*boe|cash operating costs")
    if opposed(prod, realise):
        add("volume_versus_price",
            "Production and realised price per barrel are moving in opposite directions",
            [prod, realise])
    if opposed(realise, unitcost) or (realise and unitcost
                                      and realise.latest_accel and unitcost.latest_accel
                                      and realise.latest_accel > 0 and unitcost.latest_accel > 0
                                      and unitcost.significant and realise.significant):
        add("price_and_unit_cost_both_rising"
            if (realise and unitcost and realise.latest_accel and unitcost.latest_accel
                and realise.latest_accel > 0 and unitcost.latest_accel > 0)
            else "price_versus_unit_cost",
            "Realised price per barrel and unit cost are both accelerating"
            if (realise and unitcost and realise.latest_accel and unitcost.latest_accel
                and realise.latest_accel > 0 and unitcost.latest_accel > 0)
            else "Realised price per barrel and unit cost are moving in opposite directions",
            [realise, unitcost])

    opratio = _find(series, name_pat=r"operating ratio")
    carloads = _find(series, name_pat=r"carloads?|\brtm\b|\bgtm")
    perload = _find(series, name_pat=r"revenue\s*/\s*carload|per carload")
    if counter_polarity(rev, opratio) and (rev.latest_accel or 0) > 0:
        add("revenue_up_operating_ratio_up",
            "Revenue and the operating ratio are both rising, so cost is growing "
            "with the top line", [rev, opratio])
    fuel = _find(series, name_pat=r"fuel (gallon|consum)|gallons consumed")
    traffic = _find(series, name_pat=r"\brtm\b|\bgtm\b|ton-?miles|revenue ton")
    if (fuel and traffic and fuel.significant and fuel.latest_growth is not None
            and traffic.latest_growth is not None
            and fuel.latest_growth - traffic.latest_growth > 2.0):
        add("fuel_outpacing_traffic",
            "Fuel consumption is growing faster than traffic output, so the "
            "haul is consuming more fuel per unit moved", [fuel, traffic])
    lf = _find(series, name_pat=r"load factor")
    unit_rev = _find(series, name_pat=r"\bprasm\b|passenger revenue per available seat")
    pax_rev = _find(series, name_pat=r"passenger\s*-?\s*revenue")
    lf_pair = unit_rev or pax_rev
    if opposed(lf, lf_pair):
        add("load_factor_versus_unit_revenue",
            "Load factor and passenger unit revenue are moving in opposite directions",
            [lf, lf_pair])

    if opposed(carloads, perload):
        add("volume_versus_price_per_load",
            "Volume and revenue per carload are moving in opposite directions",
            [carloads, perload])
    if opposed(rev, opratio):
        add("revenue_versus_operating_ratio",
            "Revenue and the operating ratio are moving in opposite directions",
            [rev, opratio])

    aum = _find(series, name_pat=r"\baum\b|assets under management")
    flows = _find(series, name_pat=r"^net flows?\b|net new (assets|money)")
    basefee = _find(series, name_pat=r"base fee|investment advisory")
    cashline = fcf or ocf
    REV_CASH_GAP_PP = 20.0
    if (rev and cashline and rev.latest_growth is not None
            and cashline.latest_growth is not None
            and rev.kind == "level" and cashline.kind == "level"
            and (rev.latest_growth - cashline.latest_growth) > REV_CASH_GAP_PP):
        add("revenue_outrunning_cash",
            "Revenue and cash flow growth have separated materially",
            [rev, cashline])
    elif opposed(rev, cashline) or counter_polarity(rev, cashline):
        add("revenue_versus_cash",
            "Revenue and cash generation are moving in opposite directions",
            [rev, cashline])
    if opposed(flows, aum):
        add("flows_versus_aum",
            "The flow rate and the asset base are moving in opposite directions",
            [flows, aum])
    if opposed(basefee, aum):
        add("base_fee_versus_aum",
            "Base fees and the asset base are moving in opposite directions",
            [basefee, aum])

    mlr = _find(series, name_pat=r"medical (loss|care) ratio|\bmlr\b")
    members = _find(series, name_pat=r"^total (average )?membership")
    if opposed(mlr, members):
        add("loss_ratio_versus_membership",
            "The medical loss ratio and membership are moving in opposite directions",
            [mlr, members])
    if opposed(rev, mlr):
        add("revenue_versus_loss_ratio",
            "Revenue and the medical loss ratio are moving in opposite directions",
            [rev, mlr])

    _rev_lvl = abs(rev.latest_value) if (rev and rev.latest_value) else None
    seg = [s for s in series
           if s.rankable and s.category.startswith("SD") and s.kind == "level"
           and not s.ltcode.endswith("-M")
           and re.search(r"revenue|sales", s.name or "", re.I)
           and not re.search(r"expense|cost|capex|price|volume|realization|beginning|"
                             r"ending|bridge|"
                             r"realisation|intersegment|elimination|\bother\b|"
                             r"currency|fx\b|acquisition", s.name or "", re.I)
           and _rev_lvl and s.latest_value is not None
           and abs(s.latest_value) >= 0.03 * _rev_lvl]
    def _geo(x):
        return bool(re.search(r"geo", x.ltcode or "", re.I) or GEO_CUT_PAT.search(x.name or "")
                    or re.search(r"\beame\b|\bapac\b|\bemea\b", x.name or "", re.I))
    _best = None
    for _dim in (False, True):
        up = [s for s in seg if s.z >= STRONG_Z and _geo(s) == _dim]
        dn = [s for s in seg if s.z <= -STRONG_Z and _geo(s) == _dim]
        if up and dn:
            u = max(up, key=lambda s: s.z)
            d = min(dn, key=lambda s: s.z)
            if _best is None or (u.z - d.z) > (_best[0].z - _best[1].z):
                _best = (u, d)
    if _best:
        add("segment_divergence",
            "Reported segments are moving in opposite directions", list(_best))

    named = {m["ltcode"] for d in out for m in d["metrics"]}
    signed = [s for s in series
              if s.rankable and s.polarity != 0 and s.significant
              and s.latest_accel is not None and abs(s.z or 0) >= MATERIAL_Z
              and not s.category.startswith("BS") and (s.tier or 3) <= 2
              and not re.search(r"compensation|\bsbc\b", s.name or "", re.I)
              and not (s.category.startswith("SD")
                       and re.search(r"expense|cost", s.name or "", re.I))]
    signed.sort(key=lambda s: (s.tier, -abs(s.z or 0)))
    pos = [s for s in signed if s.polarity > 0][:2]
    neg = [s for s in signed if s.polarity < 0][:2]
    for a in pos:
        for b in neg:
            if len(out) >= 3:
                break
            if a.ltcode in named and b.ltcode in named:
                continue
            if not counter_polarity(a, b):
                continue
            rising = (a.latest_accel or 0) > 0
            if rising and (b.latest_growth is None or b.latest_growth <= 0):
                continue
            if not rising and (a.latest_growth is None or a.latest_growth >= 0):
                continue
            if b.kind == "ratio" and re.search(r"%\s*revenue|as % of revenue", b.name or "", re.I) \
                    and b.latest_value is not None and abs(b.latest_value) < 5.0:
                continue
            add("counter_polarity",
                a.display + " and " + b.display + " are both " +
                ("accelerating" if rising else "decelerating") +
                ", which points opposite ways for the business because "
                + b.display + " is a line where lower is better",
                [a, b])
            named.update({a.ltcode, b.ltcode})

    return out


GUIDE_NUM_PAT = re.compile(r"-?[\d,]*\.?\d+(?:\s*[KMBT]\b)?\s*%?", re.I)
QUALITATIVE_GUIDE_PAT = re.compile(
    r"\b(low|mid|high|higher|upper|lower|flat|similar|slightly|modest|"
    r"stronger|weaker|better|worse|bottom|top|middle|half)\b", re.I)
HEADWIND_PAT = re.compile(r"\bheadwinds?\b", re.I)
PERIOD_REF_PAT = re.compile(
    r"\b[1-4]\s*Q\s*'?\d{2}(?:\d{2})?\b|\bQ[1-4]\s*'?\d{2}(?:\d{2})?\b"
    r"|\b[HF]Y\s*'?\d{2}(?:\d{2})?\b|\b[12]H\s*'?\d{2}(?:\d{2})?\b"
    r"|(?<=[a-z]\s)(?:19|20)\d{2}\b(?!\s*[%KMBT$])", re.I)
PAREN_NEG_PAT = re.compile(r"\(\s*\$?\s*([\d,]*\.?\d+\s*[KMBT]?\s*%?)\s*\)", re.I)
BAND_PAT = re.compile(r"(\+/-|\u00b1)\s*[\d.]+\s*%")
RANGE_PAT = re.compile(r"\bto\b|\u2013|\u2014|(?<=[\d%])\s*-\s*(?=[\d$-])")
SCALE_CODE = {"10^0": 1.0, "1": 1.0, "10^3": 1e3, "10^6": 1e6, "10^9": 1e9, "10^12": 1e12}


def guide_scale(row):
    s = str(row.get("SCALE") if row.get("SCALE") is not None
            else row.get("scale") or "").strip()
    if not s:
        return 1.0
    if s in SCALE_CODE:
        return SCALE_CODE[s]
    m = re.fullmatch(r"10\^(-?\d{1,2})", s)
    if m:
        return float(10 ** int(m.group(1)))
    if re.fullmatch(r"1(?:0+)?(?:\.0+)?", s):
        try:
            return float(s)
        except ValueError:
            return None
    return None


def parse_guide_range(row):
    text = row.get("GUIDE_TEXT") or row.get("guide_text")
    if not text:
        return None
    t = re.sub(r"\s+", " ", str(text)).strip()
    t = re.sub(r"(?<=[a-z\)])\d(?=[,.;:]|\s|$)", "", t)
    if QUALITATIVE_GUIDE_PAT.search(t):
        return None
    _code = str(row.get("LTCODE") or row.get("ltcode") or "")
    if CAGR_GUIDE_PAT.search(t) or (CHANGE_GUIDE_PAT.search(t) and "%" not in t
                                     and not _code.endswith("-Y")):
        return None
    t = PERIOD_REF_PAT.sub(" ", t)
    t = PAREN_NEG_PAT.sub(lambda m: "-" + m.group(1), t)
    nums = []
    for m in GUIDE_NUM_PAT.finditer(t.replace("$", " ")):
        tok = m.group(0).strip()
        if not re.search(r"\d", tok):
            continue
        v, _ = parse_value(tok)
        if v is not None:
            nums.append(v)
    if not nums:
        return None
    if HEADWIND_PAT.search(t) and all(v > 0 for v in nums):
        nums = [-v for v in nums]
    if BAND_PAT.search(t):
        lo = hi = nums[0]
    elif RANGE_PAT.search(t) and len(nums) >= 2:
        lo, hi = sorted(nums[:2])
    else:
        lo = hi = nums[0]
    fmt = str(row.get("FORMAT_CODE") or row.get("format_code") or "").strip().upper()[:1]
    _own_mag = bool(re.search(r"\d\s*(?:[kmbt]\b|thousand|million|billion|trillion)", t, re.I))
    if fmt != "P" and "%" not in t.split("+/-")[0] and not _own_mag:
        k = guide_scale(row)
        if k is None:
            return None
        lo, hi = lo * k, hi * k
    return (lo, hi)


CHANGE_GUIDE_PAT = re.compile(
    r"^\s*(approximately\s+)?(increase|decrease|grow|growth|decline|up|down|higher|lower)\b", re.I)
CAGR_GUIDE_PAT = re.compile(
    r"\bcagr\b|%\s*\+?\s*(/|per)\s*year|\bper year growth\b|\boff an? \d{4} base\b", re.I)


SHARE_COUNT_PAT = re.compile(
    r"shares? outstanding|share count|\bfds\b|\b(diluted|basic|weighted[- ]average) shares\b|"
    r"^\s*shares?\b(?!\s*repurchase)|\(#\)|headcount|\bunits?\b(?!\s*price)", re.I)


def _guide_readable(g, rng, s, yoy_form):
    out = _guide_readable_core(g, rng, s, yoy_form)
    if not out:
        return out
    t = str(g.get("GUIDE_TEXT") or g.get("guide_text") or "")
    if re.search(r"at ?least|minimum of|^\s*>=|\u2265", t, re.I):
        return "at least " + out
    if re.search(r"greater than|more than|in excess of|^\s*>|\bexceed", t, re.I):
        return "above " + out
    if re.search(r"less than|up to|no more than|^\s*<", t, re.I):
        return "up to " + out
    if re.search(r"\bapprox|\baround\b|\babout\b|~", t, re.I) and " to " not in out:
        return "approx. " + out
    _pm = re.search(r"\+/-\s*([\d.]+)\s*(%?)", t)
    if _pm and " to " not in out:
        return out + " \u00b1 " + _pm.group(1) + _pm.group(2)
    return out


def _guide_readable_core(g, rng, s, yoy_form):
    if rng is None:
        return None
    lo, hi = rng
    if yoy_form or str(g.get("FORMAT_CODE") or g.get("format_code") or "").upper()[:1] == "P":
        _t = re.sub(r"\+/-\s*[\d.]+\s*%?", " ", str(g.get("GUIDE_TEXT") or g.get("guide_text") or ""))
        _dps = [len(m.group(1)) for m in re.finditer(r"\d\.(\d+)", _t)]
        _dp = min(2, max(_dps)) if _dps else (0 if re.search(r"\d", _t) else 2)
        return _range_str(lo, hi, "%", _dp)
    if str(g.get("FORMAT_CODE") or g.get("format_code") or "").upper()[:1] == "X":
        f = lambda v: ("%.1fx" % v)
        return f(lo) if lo == hi else f(lo) + " to " + f(hi)
    if s is not None and s.unit_info and s.unit_info[0] not in (None, "currency"):
        lab, div, dp = s.unit_info[0], s.unit_info[1], s.unit_info[2]
        return _range_str(lo, hi, lab, dp, bps_unit=(s.unit == "bps"), div=div)
    _nm = str((s.name if s is not None else "") or g.get("LINEITEM") or g.get("lineitem") or "")
    if hi <= 0 and ((s is not None and getattr(s, "negative_convention", False))
                    or re.search(r"capex|capital expend", _nm, re.I)):
        lo, hi = -hi, -lo
    cur = (g.get("CURRENCY") or g.get("currency") or
           (getattr(s, "currency", None) if s is not None else None))
    if SHARE_COUNT_PAT.search(
            str((s.name if s is not None else "") or g.get("LINEITEM") or g.get("lineitem") or "")):
        return (_money(lo, "") if lo == hi else _money(lo, "") + " to " + _money(hi, ""))
    sym = {"USD": "$", "JPY": "\u00a5", "EUR": "\u20ac", "GBP": "\u00a3"}.get(str(cur or "").upper(), cur or "") \
        or (_DEFAULT_CUR["sym"] or "")
    _gt_raw = str(g.get("GUIDE_TEXT") or g.get("guide_text") or "")
    _src_money = any(c in _gt_raw for c in "$\u00a5\u20ac\u00a3")
    _line_money = bool(s is not None and (s.unit_info or [None])[0] == "currency")
    _plain = (str(g.get("FORMAT_CODE") or g.get("format_code") or "").upper()[:1] == "N"
              and not (g.get("CURRENCY") or g.get("currency")))
    if not _src_money and not _line_money and (s is not None or is_count_name(_nm) or re.search(
            r"volume|vehicle|production|units?|employees|subscribers|customers|deliveries",
            _nm, re.I)):
        sym = ""
    if not _src_money and not _line_money and _plain and is_count_name(_nm):
        sym = ""
    if sym == "" and all(abs(v) < 1e3 and float(v).is_integer() for v in (lo, hi)):
        return ("%d" % lo) if lo == hi else "%d to %d" % (lo, hi)
    return (_money(lo, sym) if lo == hi else _money(lo, sym) + " to " + _money(hi, sym))


def guide_history(guides):
    by = {}
    for g in guides or []:
        target = str(g.get("FISCALPERIOD") or g.get("fiscal_period") or "").strip()
        code = g.get("LTCODE") or g.get("ltcode")
        if not code or not re.fullmatch(r"(19|20)\d{2}", target):
            continue
        sp = clean_period_label(g.get("SOURCE_PERIOD") or g.get("source_period") or "")
        rng = parse_guide_range(g)
        key = rng if rng is not None else re.sub(r"\s+", " ", str(
            g.get("GUIDE_TEXT") or g.get("guide_text") or "")).strip().lower()
        by.setdefault("%s|%s" % (code, target), []).append(
            (str(g.get("GUIDEDATE") or g.get("guide_date") or ""), _quarter_sort_key(sp), sp, key))
    out = {}
    for k, rows in by.items():
        rows.sort(key=lambda t: (t[0], t[1]))
        periods = []
        for r in rows:
            if r[2] and r[2] not in periods:
                periods.append(r[2])
        out[k] = {"periods": periods, "dates": len({r[0] for r in rows}),
                  "unchanged": len({repr(r[3]) for r in rows}) == 1}
    return out


def guidance_revisions(guides, series_by_code, latest_quarter):
    by = {}
    for g in guides or []:
        target = str(g.get("FISCALPERIOD") or g.get("fiscal_period") or "").strip()
        if not re.fullmatch(r"(19|20)\d{2}", target):
            continue
        code = g.get("LTCODE") or g.get("ltcode")
        rng = parse_guide_range(g)
        if not code or rng is None:
            continue
        by.setdefault((code, target), []).append((
            str(g.get("GUIDEDATE") or g.get("guide_date") or ""),
            _quarter_sort_key(g.get("SOURCE_PERIOD") or g.get("source_period")), g, rng))
    out = []
    for (code, target), rows in by.items():
        rows.sort(key=lambda t: (t[0], t[1]))
        if len(rows) < 2:
            continue
        _k = max((i for i in range(1, len(rows)) if rows[i][3] != rows[i - 1][3]), default=None)
        if _k is None:
            continue
        year_first, first, changed, last = rows[0], rows[_k - 1], rows[_k], rows[-1]
        _back = year_first if (year_first[3] == last[3] and _k > 1) else None
        m0 = (first[3][0] + first[3][1]) / 2.0
        m1 = (last[3][0] + last[3][1]) / 2.0
        base = code.split("-")[0]
        s = series_by_code.get(code) or series_by_code.get(base)
        yoy = code.endswith("-Y")
        name = (s.name if (s is not None and not yoy) else
                (last[2].get("LINEITEM") or last[2].get("lineitem") or code))
        neg = bool(s is not None and getattr(s, "negative_convention", False) and m1 < 0 and m0 < 0)
        d1, d0 = (abs(m1), abs(m0)) if neg else (m1, m0)
        direction = ("raised" if d1 > d0 else "lowered") if d1 != d0 else "range changed"
        _t0 = str(first[2].get("GUIDE_TEXT") or first[2].get("guide_text") or "")
        _t1 = str(last[2].get("GUIDE_TEXT") or last[2].get("guide_text") or "")
        _floor = all(re.search(r"greater than|more than|at ?least|minimum of|in excess of|^\s*>|\bexceed", _t, re.I)
                     for _t in (_t0, _t1))
        _ceil = all(re.search(r"less than|up to|no more than|below|^\s*<", _t, re.I)
                    for _t in (_t0, _t1))
        bound = "floor" if _floor else ("ceiling" if _ceil else None)
        _BND = r"greater than|more than|at ?least|minimum of|in excess of|^\s*>|\bexceed"
        _f0, _f1 = bool(re.search(_BND, _t0, re.I)), bool(re.search(_BND, _t1, re.I))
        _one_sided = (_f0 != _f1)
        if bound and d1 != d0:
            direction = bound + (" raised" if d1 > d0 else " lowered")
        _headwind = (not neg and m0 < 0 and m1 < 0 and not bound)
        if _headwind and d1 != d0:
            direction = "headwind narrowed" if m1 > m0 else "headwind widened"
        is_pct = bool(yoy or str(last[2].get("FORMAT_CODE") or last[2].get("format_code")
                                 or "").upper()[:1] == "P")
        _flip = (m0 < 0 < m1) or (m1 < 0 < m0)
        _mids = [(r[3][0] + r[3][1]) / 2.0 for r in rows]
        _flip_year = any(a < 0 < b or b < 0 < a for a, b in zip(_mids, _mids[1:]))
        if _flip:
            direction = "changed sign"
        _back_to = ((year_first[2].get("SOURCE_PERIOD") or year_first[2].get("source_period") or "first")
                    if _back is not None else None)
        out.append({
            "is_percentage": is_pct,
            "midpoint_change_bps": (round((m1 - m0) * 100.0, 1)
                                    if is_pct and not bound and not _one_sided and not _flip else None),
            "changed_in": changed[2].get("SOURCE_PERIOD") or changed[2].get("source_period"),
            "back_to": _back_to,
            "sign_changed_in_year": _flip_year,
            "year_first_display": None if (_back_to or year_first[3] == first[3]) else _match_precision(
                (str(_guide_readable(year_first[2], year_first[3], s, yoy))
                 if _guide_readable(year_first[2], year_first[3], s, yoy) else
                 year_first[2].get("GUIDE_TEXT") or year_first[2].get("guide_text")),
                year_first[3], (str(_guide_readable(last[2], last[3], s, yoy))
                                if _guide_readable(last[2], last[3], s, yoy) else None)),
            "year_first_source_period": (year_first[2].get("SOURCE_PERIOD")
                                         or year_first[2].get("source_period"))
                                        if (year_first is not first and year_first[3] != first[3]) else None,
            "ltcode": code, "display": (s.display if (s is not None and not yoy
                                                     and not getattr(s, "_label_proved", False))
                                        else display_name(name)),
            "name": name,
            "target_period": target,
            "first_text": last and (first[2].get("GUIDE_TEXT") or first[2].get("guide_text")),
            "first_display": _match_precision(
                (str(_guide_readable(first[2], first[3], s, yoy))
                 if _guide_readable(first[2], first[3], s, yoy) else None),
                first[3], (str(_guide_readable(last[2], last[3], s, yoy))
                           if _guide_readable(last[2], last[3], s, yoy) else None)),
            "first_source_period": first[2].get("SOURCE_PERIOD") or first[2].get("source_period"),
            "first_date": first[0],
            "latest_text": last[2].get("GUIDE_TEXT") or last[2].get("guide_text"),
            "latest_display": (str(_guide_readable(last[2], last[3], s, yoy))
                               if _guide_readable(last[2], last[3], s, yoy) else None),
            "latest_source_period": last[2].get("SOURCE_PERIOD") or last[2].get("source_period"),
            "latest_date": last[0],
            "direction": direction,
            "midpoint_change_pct": (((d1 - d0) / abs(d0) * 100.0) if (d0 and not is_pct
                                                                     and not bound
                                                                     and not _one_sided
                                                                     and not _flip)
                                    else None),
            "bound": bound,
            "revisions": len(rows) - 1,
            "in_scan": s is not None,
            "latest_growth": _guide_growth(s),
            "unit": (s.unit if s is not None else "pct"),
        })
    out += _worded_revisions(guides, series_by_code, {(r["ltcode"], r["target_period"]) for r in out})
    out.sort(key=lambda r: (r["target_period"], r["display"]))
    return out


_WORD_POS = {"low": 2.0, "mid": 5.0, "high": 8.0, "upper": 8.0}


def _match_precision(disp, rng, latest_disp):
    if not disp or not latest_disp or rng is None or "%" not in disp or "%" not in latest_disp:
        return disp
    _dps = [len(m.group(1)) for m in re.finditer(r"\d\.(\d+)%", latest_disp)]
    dp = max(_dps) if _dps else 0
    lo, hi = rng
    if any(round(v, dp) != round(v, 6) for v in (lo, hi) if v is not None):
        return disp
    pre = re.match(r"^(above |at least |up to |approx\. )", disp)
    body = _range_str(lo, hi, "%", dp)
    return (pre.group(1) if pre else "") + body


def _guide_placeholder(display, text, scale):
    if display:
        return str(display)
    t = str(text or "")
    if not t:
        return ""
    if re.search(r"\d", t) and str(scale or "").strip().lower() not in ("", "1", "10^0", "1.0"):
        return None
    return t


def _guide_growth(s):
    if s is None or getattr(s, "modelled_input", False) or getattr(s, "modelled", False):
        return None
    if s.kind == "ratio" and s.provenance == "derived" and re.search(r"\betr\b|tax rate", s.name or "", re.I):
        return None
    return s.latest_growth


def _worded_level(text):
    t = re.sub(r"[\s\-]+", " ", str(text or "").lower().replace("'", "").replace("\u2019", ""))
    _AB = {"l": "low", "m": "mid", "h": "high"}
    t = re.sub(r"\b([lmh])([sd])d\s+(?:to\s+)?([lmh])\2d\b",
               lambda m: _AB[m.group(1)] + " to " + _AB[m.group(3)] + " "
               + ("single" if m.group(2) == "s" else "double") + " digit", t)
    t = re.sub(r"\b([lmh])([sd])d\b",
               lambda m: _AB[m.group(1)] + " " + ("single" if m.group(2) == "s" else "double") + " digit", t)
    vals = []
    _DEC = {"twenties": 20, "thirties": 30, "forties": 40, "fifties": 50}
    for m in re.finditer(r"(?:\b(high|upper|top|low) end of )?\b(low|mid|high|upper)"
                         r"(?: (?:to|and) (low|mid|high|upper))? "
                         r"(single|double|teens|twenties|thirties|forties|fifties|(\d)0 ?s)\b(?: digits?)?", t):
        end, w1, w2, kind = m.group(1), m.group(2), m.group(3), m.group(4)
        def v(w):
            if kind == "single":
                return _WORD_POS[w]
            if kind == "double":
                return {"low": 11.0, "mid": 15.0, "high": 18.0, "upper": 18.0}[w]
            if kind == "teens":
                return {"low": 13.0, "mid": 15.0, "high": 18.0, "upper": 18.0}[w]
            if kind in _DEC:
                return _DEC[kind] + _WORD_POS[w]
            return int(m.group(5)) * 10 + _WORD_POS[w]
        x = (v(w1) + v(w2)) / 2.0 if w2 else v(w1)
        if end:
            x += (-1.0 if end == "low" else 1.0)
        vals.append(x)
    if vals:
        return sum(vals) / len(vals)
    if re.search(r"\bflat\b", t):
        return 0.0
    _n = re.fullmatch(r"\s*(?:approx\.?|approximately|about|around)?\s*\(?(-?\d+(?:\.\d+)?)\)?\s*%\s*", t)
    if _n:
        return float(_n.group(1)) * (-1 if "(" in t else 1)
    m = re.search(r"(top|upper|bottom|low) end of (?:the )?(\d+(?:\.\d+)?) (?:to|and|\-) (\d+(?:\.\d+)?) ?(?:percent|%)", t)
    if m:
        return float(m.group(3)) if m.group(1) in ("top", "upper") else float(m.group(2))
    for pat, val in ((r"near the top|top end of (?:the )?(?:annual )?target", 0.9),
                     (r"top half|upper half", 0.75), (r"middle|midpoint", 0.5),
                     (r"bottom half|lower half", 0.25),
                     (r"near the bottom|bottom end|low end of (?:the )?(?:annual )?target", 0.1)):
        if re.search(pat, t):
            return val
    return None


def _worded_revisions(guides, series_by_code, have):
    by = {}
    for g in guides or []:
        target = str(g.get("FISCALPERIOD") or g.get("fiscal_period") or "").strip()
        if not re.fullmatch(r"(19|20)\d{2}", target):
            continue
        code = g.get("LTCODE") or g.get("ltcode")
        txt = str(g.get("GUIDE_TEXT") or g.get("guide_text") or "").strip()
        if not code or not txt:
            continue
        by.setdefault((code.split("-")[0] if not code.endswith("-M") else code, target), []).append((
            str(g.get("GUIDEDATE") or g.get("guide_date") or ""),
            _quarter_sort_key(g.get("SOURCE_PERIOD") or g.get("source_period")), g, txt))
    out = []
    norm = lambda x: re.sub(r"[^a-z0-9]+", " ", re.sub(r"(?<=[a-z])\d\b", "",
                                                     x.lower().replace("'", "").replace("\u2019", ""))).strip()
    for (key, target), rows in by.items():
        rows.sort(key=lambda t: (t[0], t[1]))
        per = {}
        for r in rows:
            per[r[1]] = r
        rows = [per[k] for k in sorted(per)]
        if len(rows) < 2:
            continue
        if all(parse_guide_range(r[2]) is not None for r in rows):
            continue
        _k = max((i for i in range(1, len(rows)) if norm(rows[i][3]) != norm(rows[i - 1][3])),
                 default=None)
        if _k is None:
            continue
        year_first, first, changed, last = rows[0], rows[_k - 1], rows[_k], rows[-1]
        code = last[2].get("LTCODE") or last[2].get("ltcode")
        if (code, target) in have:
            continue
        l0, l1 = _worded_level(first[3]), _worded_level(last[3])
        if l0 is not None and l1 is not None and l0 != l1:
            direction = "raised" if l1 > l0 else "lowered"
        else:
            direction = "wording changed"
        base = code.split("-")[0]
        s = series_by_code.get(code) or series_by_code.get(base)
        yoy = code.endswith("-Y")
        name = (s.name if (s is not None and not yoy) else
                (last[2].get("LINEITEM") or last[2].get("lineitem") or code))

        def _side_display(row):
            _rng = parse_guide_range(row)
            if _rng is None:
                return None
            _d = _guide_readable(row, _rng, s, yoy)
            return str(_d) if _d else None
        _first_disp, _last_disp = _side_display(first[2]), _side_display(last[2])
        out.append({
            "is_percentage": False, "midpoint_change_bps": None, "midpoint_change_pct": None,
            "ltcode": code,
            "display": (s.display if (s is not None and not yoy
                                      and not getattr(s, "_label_proved", False))
                        else display_name(name)),
            "name": name, "target_period": target, "worded": True,
            "first_text": re.sub(r"(?<=[a-z])\d\b", "", re.sub(r"\s+", " ", first[3])),
            "first_display": _first_disp,
            "first_source_period": first[2].get("SOURCE_PERIOD") or first[2].get("source_period"),
            "first_date": first[0],
            "latest_text": re.sub(r"(?<=[a-z])\d\b", "", re.sub(r"\s+", " ", last[3])),
            "latest_display": _last_disp,
            "first_scale": str(first[2].get("SCALE") or first[2].get("scale") or ""),
            "latest_scale": str(last[2].get("SCALE") or last[2].get("scale") or ""),
            "latest_source_period": last[2].get("SOURCE_PERIOD") or last[2].get("source_period"),
            "latest_date": last[0], "direction": direction, "bound": None,
            "changed_in": changed[2].get("SOURCE_PERIOD") or changed[2].get("source_period"),
            "year_first_display": (re.sub(r"\s+", " ", year_first[3])
                                   if year_first is not first and norm(year_first[3]) != norm(first[3])
                                   and norm(year_first[3]) != norm(last[3]) else None),
            "year_first_source_period": ((year_first[2].get("SOURCE_PERIOD")
                                          or year_first[2].get("source_period"))
                                         if year_first is not first else None),
            "revisions": len(rows) - 1, "in_scan": s is not None,
            "latest_growth": _guide_growth(s),
            "unit": (s.unit if s is not None else "pct"),
        })
    return out


def parse_guide_midpoint(row):
    rng = parse_guide_range(row)
    if rng is None:
        return None
    lo, hi = rng
    return lo if lo == hi else (lo + hi) / 2.0


def next_quarter(label):
    m = re.fullmatch(r"([1-4])Q(\d{2})", (label or "").strip().upper())
    if not m:
        return None
    q, yy = int(m.group(1)), int(m.group(2))
    q += 1
    if q == 5:
        q, yy = 1, (yy + 1) % 100
    return f"{q}Q{yy:02d}"


UNCOMPARED_STATUS = {
    "line_not_in_scan": "guided line not among the lines this scan retrieved",
    "line_not_scoreable": "too little history on this line to score",
    "qualitative_guide": "no figure in the guide text",
    "growth_form_not_comparable": "guide is a growth rate, line is measured in bps",
    "level_withheld": "this line's level is withheld",
    "scale_mismatch": "guide and reported figure are not on the same scale",
    "actual_is_calculated": "the figure in the scan is an InSync calculation, not the company's own",
}


ZERO_IS_MISSING_FLOOR = 0.10


BRIDGES = {
    "IS_P_114": (("IS_P_110", "Adjusted EBIT"), ("IS_P_111", "D&A")),
    "IS_P_113": (("IS_P_109", "EBIT"), ("IS_P_111", "D&A")),
}
BRIDGE_TOL = 0.005


def _attach_bridges(payload, series_by_code):
    for key in ("headline_moves", "accelerating", "decelerating", "deep_dive",
                "primary_context"):
        for r in payload.get(key) or []:
            parts = BRIDGES.get(r.get("ltcode"))
            if not parts or r.get("bridge"):
                continue
            total = r.get("latest_value")
            vals = []
            for code, label in parts:
                s = series_by_code.get(code)
                v = getattr(s, "latest_value", None) if s else None
                if v is None:
                    vals = None
                    break
                vals.append((label, v))
            if not vals or not total:
                continue
            got = sum(v for _, v in vals)
            if abs(got - total) > BRIDGE_TOL * abs(total):
                continue
            r["bridge"] = {
                "parts": [{"label": l, "value": v, "display": _money(v)} for l, v in vals],
                "total": total, "total_display": _money(total),
                "display": (" + ".join("%s %s" % (l, _money(v)) for l, v in vals)
                            + " = " + _money(total)),
            }


def _attach_revenue_decomposition(payload, series_by_code):
    allser = list(series_by_code.values())
    total = series_by_code.get("IS_P_101") or next(
        (x for x in allser if re.match(r"^total revenue$|^revenue$", x.name or "", re.I)), None)
    if total is None or not total.latest_value:
        return
    others = [x for x in allser if x.kind == "level" and x.latest_value
              and re.search(r"collaborat|royalt|other revenue|& other revenue|"
                            r"alliance revenue|license revenue", x.name or "", re.I)
              and not re.search(r"cost|expense|%", x.name or "", re.I)]
    product = next((x for x in allser if x.kind == "level" and x.latest_value
                    and re.search(r"(net )?product (revenue|sales)", x.name or "", re.I)), None)
    for key in ("headline_moves", "accelerating", "decelerating", "deep_dive",
                "primary_context"):
        for r in payload.get(key) or []:
            if (not r.get("bridge") and product is not None
                    and r.get("ltcode") == total.ltcode):
                for o in others:
                    if abs(product.latest_value + o.latest_value - total.latest_value) \
                            <= BRIDGE_TOL * abs(total.latest_value):
                        cur = getattr(total, "currency", None) or ""
                        r["bridge"] = {
                            "kind": "decomposition",
                            "parts": [{"label": product.display, "value": product.latest_value,
                                       "display": _money(product.latest_value, cur)},
                                      {"label": o.display, "value": o.latest_value,
                                       "display": _money(o.latest_value, cur)}],
                            "total": total.latest_value,
                            "total_display": _money(total.latest_value, cur),
                            "display": "%s %s + %s %s = %s %s" % (
                                product.display, _money(product.latest_value, cur),
                                o.display, _money(o.latest_value, cur),
                                total.display, _money(total.latest_value, cur)),
                        }
                        break
                continue
            if r.get("bridge") or not re.search(r"(net )?product (revenue|sales)",
                                                r.get("name") or "", re.I):
                continue
            v = r.get("latest_value")
            if not v:
                continue
            for o in others:
                if abs(v + o.latest_value - total.latest_value) <= BRIDGE_TOL * abs(total.latest_value):
                    cur = getattr(total, "currency", None) or ""
                    r["bridge"] = {
                        "kind": "decomposition",
                        "parts": [{"label": r.get("display"), "value": v, "display": _money(v, cur)},
                                  {"label": o.display, "value": o.latest_value,
                                   "display": _money(o.latest_value, cur)}],
                        "total": total.latest_value,
                        "total_display": _money(total.latest_value, cur),
                        "display": "%s %s + %s %s = %s %s" % (
                            r.get("display"), _money(v, cur), o.display,
                            _money(o.latest_value, cur), total.display,
                            _money(total.latest_value, cur)),
                    }
                    break


EXCL_SUFFIX_PAT = re.compile(r"\s*\((?:excl\.?|excluding|ex-)\s*([^)]+)\)\s*$", re.I)


def company_exclusions(series):
    by_name = {(x.name or "").strip().lower(): x for x in series}
    out = []
    for x in series:
        m = EXCL_SUFFIX_PAT.search(x.name or "")
        if not m or re.search(r"1x|operating lease|incl", m.group(1), re.I):
            continue
        base = by_name.get((x.name or "")[:m.start()].strip().lower())
        if base is None or base.kind != x.kind:
            continue
        pb = dict(zip(base.periods or [], base.values or []))
        px = dict(zip(x.periods or [], x.values or []))
        common = [p for p in (x.periods or []) if p in pb and pb[p] is not None
                  and px.get(p) is not None]
        if len(common) < 2:
            continue
        last = common[-1]
        def _k(p):
            return _quarter_sort_key(clean_period_label(p))
        ly, lq = _k(last)
        prior = next((p for p in reversed(common[:-1]) if _k(p) == (ly - 1, lq)), None) \
            if ly >= 0 else None
        cur = getattr(base, "currency", None) or ""
        def _f(v):
            return (cur + ("%.2f" % v)) if abs(v) < 1000 else _money(v, cur)
        row = {"base_ltcode": base.ltcode, "base_display": base.display,
               "excl_ltcode": x.ltcode, "item": m.group(1).strip(),
               "period": last, "base_value": pb[last], "excl_value": px[last],
               "gap": px[last] - pb[last],
               "base_display_value": _f(pb[last]), "excl_display_value": _f(px[last]),
               "gap_display": _f(px[last] - pb[last])}
        if prior is not None:
            row.update({"prior_period": prior, "prior_gap": px[prior] - pb[prior],
                        "prior_gap_display": _f(px[prior] - pb[prior])})
        out.append(row)
    _pr = by_name.get("net promote income")
    _cf = by_name.get("core ffo")
    _cps = by_name.get("core ffo/share")
    if _pr is not None and _cf is not None and _cps is not None:
        pp_ = dict(zip(_pr.periods, _pr.values))
        pc_ = dict(zip(_cf.periods, _cf.values))
        ps_ = dict(zip(_cps.periods, _cps.values))
        com = [q for q in _cps.periods if pp_.get(q) is not None and pc_.get(q) and ps_.get(q)]
        if com:
            def _ex(q):
                sh = pc_[q] / ps_[q]
                return ps_[q] - pp_[q] / sh
            last = com[-1]
            ya = _year_ago_label(last)
            ya = ya if ya in com else next((q for q in com if clean_period_label(q) == ya), None)
            cur = getattr(_cps, "currency", None) or ""
            f = lambda v: (("-" if v < 0 else "") + cur + ("%.2f" % abs(v)))
            row = {"base_ltcode": _cps.ltcode, "base_display": _cps.display,
                   "excl_ltcode": _pr.ltcode, "item": "Net promote income",
                   "period": last, "base_value": ps_[last], "excl_value": _ex(last),
                   "gap": _ex(last) - ps_[last],
                   "base_display_value": f(ps_[last]), "excl_display_value": f(_ex(last)),
                   "gap_display": f(_ex(last) - ps_[last]), "insync_calculated": True,
                   "item_per_share_display": f(ps_[last] - _ex(last))}
            if ya:
                row.update({"prior_period": ya, "prior_gap": _ex(ya) - ps_[ya],
                            "prior_gap_display": f(_ex(ya) - ps_[ya]),
                            "prior_item_per_share_display": f(ps_[ya] - _ex(ya))})
                if ps_[ya] and _ex(ya):
                    row["base_growth_display"] = _fmt_growth((ps_[last] / ps_[ya] - 1) * 100, "pct")
                    row["excl_growth_display"] = _fmt_growth((_ex(last) / _ex(ya) - 1) * 100, "pct")
                    row["growth_gap_pp"] = abs((ps_[last] / ps_[ya]) - (_ex(last) / _ex(ya))) * 100
            out.append(row)
    seen, uniq = set(), []
    for r in sorted(out, key=lambda r: (0 if "diluted" in r["base_display"].lower() else 1,
                                        r["base_ltcode"])):
        if r["item"].lower() in seen:
            continue
        seen.add(r["item"].lower())
        uniq.append(r)
    return uniq


EXCL_TWIN_PAT = re.compile(r"^(?P<base>.+?)\s*\((?:ex-|excl\.?\s+|excluding\s+)(?P<item>[^)]+)\)\s*$", re.I)


def _exclusion_reconcile(base, twin, item, series):
    def _norm(t):
        t = re.sub(r"in-?\s?process\s*r\s*&\s*d", "ipr&d", str(t or "").lower())
        return re.sub(r"\s+", " ", re.sub(r"\([^)]*\)", " ", t)).strip()
    key = _norm(item)
    if base is None or not key:
        return "unchecked"
    cands = [x for x in series if _norm(x.name) == key and x.kind == "level"]
    if not cands:
        return "unchecked"
    per = twin.periods[-1] if twin.periods else None

    def _at(x):
        try:
            return x.values[x.periods.index(per)]
        except (ValueError, IndexError, AttributeError):
            return None
    tv, bv = _at(twin), _at(base)
    if tv is None or bv is None:
        return "unchecked"
    adj = next((x for x in series if re.match(
        r"^" + re.escape((base.name or "").strip()) + r"\s*\(ex-?\s?1x items?[^)]*\)\s*$",
        x.name or "", re.I)), None)
    av = _at(adj) if adj is not None else None
    for c in cands:
        iv = _at(c)
        if iv is None or not iv:
            continue
        tol = 0.01 * abs(iv)
        if abs((bv - tv) - iv) <= tol:
            return "item_only"
        if av is not None and abs((av - tv) - iv) <= tol:
            return "also_one_time"
    return "no"


def _attach_exclusion_twins(payload, series):
    twins = {}
    for x in series:
        m = EXCL_TWIN_PAT.match((x.name or "").strip())
        if not m or re.search(r"1x|operating lease|amortization|\bsbc\b", m.group("item"), re.I):
            continue
        if x.latest_growth is None:
            continue
        twins.setdefault(m.group("base").strip().lower(), x)
    by_code = {x.ltcode: x for x in series}
    for key in ("headline_moves", "accelerating", "decelerating", "primary_context", "deep_dive"):
        for r in payload.get(key) or []:
            t = twins.get((r.get("name") or "").strip().lower())
            if t is None or t.ltcode == r.get("ltcode"):
                continue
            item = EXCL_TWIN_PAT.match(t.name.strip()).group("item").strip()
            _fix = _exclusion_reconcile(by_code.get(r.get("ltcode")), t, item, series)
            if _fix == "no":
                continue
            if _fix == "also_one_time":
                item = item + " and one-time items"
            lvl = t.display_level
            r["exclusion_twin"] = {
                "reconciled": _fix in ("item_only", "also_one_time"),
                "ltcode": t.ltcode, "item": item,
                "display": t.display, "latest_growth": t.latest_growth, "prev_growth": t.prev_growth,
                "prev_period": t.periods[-2] if len(t.periods) >= 2 else None,
                "growth_display": _fmt_growth(t.latest_growth, t.unit),
                "prev_growth_display": _fmt_growth(t.prev_growth, t.unit),
                "level_display": (_money(lvl[0], getattr(t, "currency", None))
                                  if lvl and lvl[0] is not None and lvl[1] in (None, "currency") else None),
                "unit": t.unit}


def _raw_value(raw, code, period):
    vals = {parse_value(r.get("VALUE"))[0] for r in (raw or {}).get("actuals") or []
            if r.get("LTCODE") == code
            and re.sub(r"^R-", "", str(r.get("FISCALPERIOD") or "")) == period}
    vals.discard(None)
    return vals.pop() if len(vals) == 1 else None


def _attach_adjusted_twins(payload, series_by_code, raw=None):
    by_name = {}
    for x in series_by_code.values():
        m = re.match(r"^(.*?)\s*\(ex\s*-?\s*1x items?[^)]*\)\s*$", x.name or "", re.I)
        if m:
            by_name.setdefault(m.group(1).strip().lower(), x)
    for key in ("headline_moves", "accelerating", "decelerating", "deep_dive",
                "primary_context"):
        for r in payload.get(key) or []:
            nm = str(r.get("name") or "")
            if re.search(r"ex-?\s?1x|adjusted", nm, re.I) or r.get("adjusted_twin"):
                continue
            t = by_name.get(nm.strip().lower())
            if t is None and len(nm) >= 20:
                import difflib
                _best = max(by_name, default=None, key=lambda k: difflib.SequenceMatcher(
                    None, k, nm.strip().lower()).ratio())
                if _best and difflib.SequenceMatcher(None, _best, nm.strip().lower()).ratio() >= 0.95:
                    t = by_name[_best]
            if t is None or t.latest_growth is None or t.kind != r.get("kind"):
                continue
            tw = {"ltcode": t.ltcode, "display": t.display, "level_word": t.level_word(),
                  "latest_growth": t.latest_growth,
                  "growth_display": _fmt_growth(t.latest_growth, t.unit)}
            adj = series_by_code.get("IS_P_102")
            if (t.ltcode or "").endswith("-M") and t.periods and raw \
                    and re.search(r"1x", getattr(adj, "name", "") or "", re.I):
                q = t.periods[-1]
                ya = _year_ago_label(q)
                rv = {p: (_raw_value(raw, "IS_P_101", p), _raw_value(raw, "IS_P_102", p))
                      for p in (q, ya)}
                if any(a and b and abs(a / b - 1) > 0.005 for a, b in rv.values()):
                    n1, n0 = _raw_value(raw, t.ltcode[:-2], q), _raw_value(raw, t.ltcode[:-2], ya)
                    a1, a0 = rv[q][1], rv[ya][1]
                    if None in (n1, n0) or not a1 or not a0:
                        continue
                    g = round((n1 / a1 - n0 / a0) * 1e4, 1)
                    tw = {"ltcode": t.ltcode,
                          "display": t.display + " on revenue excluding one-time items",
                          "basis": "adjusted_revenue", "numerator_ltcode": t.ltcode[:-2],
                          "denominator_ltcode": "IS_P_102", "period": q, "year_ago_period": ya,
                          "level_word": None if g == 0 else ("widening" if g > 0 else "narrowing"),
                          "latest_growth": g, "growth_display": _fmt_growth(g, "bps")}
            r["adjusted_twin"] = tw


def _attach_reported_twins(payload, series_by_code):
    filed = [s for s in series_by_code.values()
             if s.provenance == "filed" and s.kind == "ratio"
             and s.latest_value is not None]
    for key in ("deep_dive", "headline_moves", "primary_context"):
        for r in payload.get(key) or []:
            if r.get("reported_twin") or r.get("provenance") != "derived" \
                    or r.get("kind") != "ratio" or r.get("latest_value") is None:
                continue
            me = series_by_code.get(r.get("ltcode"))
            if me is None:
                continue
            fam = _family(r.get("name"), r)
            best = None
            for s in filed:
                if s.ltcode == me.ltcode or _family(s.name, {}) != fam:
                    continue
                d = abs(s.as_points()[-1] - me.as_points()[-1]) \
                    if s.as_points() and me.as_points() else None
                if d is None or d > 0.5:
                    continue
                if best is None or d < best[1]:
                    best = (s, d)
            if best is None:
                continue
            s, d_now = best
            mine = dict(zip(me.periods, me.as_points()))
            theirs = dict(zip(s.periods, s.as_points()))
            gaps = [abs(mine[k] - theirs[k]) for k in mine
                    if k in theirs and mine[k] is not None and theirs[k] is not None]
            lv = s.as_points()[-1]
            r["reported_twin"] = {
                "ltcode": s.ltcode, "name": s.name, "display": s.display,
                "latest": lv, "latest_display": "%.1f%%" % lv,
                "gap_bps_now": round(d_now * 100.0),
                "gap_bps_max": round(max(gaps) * 100.0) if gaps else None,
            }


def _grid_gap_reason(s):
    v, p = s.values or [], s.periods or []
    if len(v) < 2 or v[-1] is None:
        return None
    unread = getattr(s, "unreadable_by_label", None) or {}

    def why(label):
        if label in unread:
            return "came back unreadable (%r)" % unread[label]
        return "is missing from the feed"
    if s.kind != "pre_growth" and len(v) >= 5 and v[-5] is None:
        code = "year_ago_unreadable" if p[-5] in unread else "year_ago_missing"
        return code, ("the year-ago quarter (%s) %s, so the latest y/y cannot be "
                      "computed" % (p[-5], why(p[-5])))
    if v[-2] is None:
        code = "prior_quarter_unreadable" if p[-2] in unread else "prior_quarter_missing"
        return code, ("last quarter (%s) %s, so the change in pace cannot be computed"
                      % (p[-2], why(p[-2])))
    if s.kind != "pre_growth" and len(v) >= 6 and v[-6] is None:
        code = ("prior_year_ago_unreadable" if p[-6] in unread else "prior_year_ago_missing")
        return code, ("the year-ago quarter for last quarter (%s) %s, so the change in "
                      "pace cannot be computed" % (p[-6], why(p[-6])))
    return None


def _exclusion_reason(s):
    return _exclusion(s)[1]


def _exclusion_reason_code(s):
    return _exclusion(s)[0]


def _exclusion(s):
    code, text = _exclusion_pre(s)
    if code:
        return code, text
    return _exclusion_core(s)


def _exclusion_pre(s):
    if getattr(s, "twin_collapsed_by", None):
        if s.twin_reason == "adjusted_twin_thin":
            return "adjusted_twin_thin", ("only %d quarters of the adjusted figure came back, so "
                                          "the reported line (%s) is scored instead"
                                          % (s.obs, s.twin_collapsed_name))
        return "reported_twin", ("reported twin of an adjusted line already scanned (%s)"
                                 % s.twin_collapsed_name)
    if getattr(s, "outside_parts", None):
        o = s.outside_parts
        return "outside_parts", ("Data-Quality Exception, Not Scored: the regional lines (%s) cannot average to "
                "the %s %s reading (%s), so one side is wrong in the source; the aggregate, the "
                "level at which the company reports, is kept and the regional lines are held out"
                % (o["parts_display"], o["period"], o.get("aggregate") or "aggregate", o["value_display"]))
    if getattr(s, "vendor_ratio_mismatch", None):
        o = s.vendor_ratio_mismatch
        return "vendor_ratio_mismatch", ("Data-Quality Exception, Not Scored: the source's %s reading "
                "(%s) disagrees with its own %s over %s (%s), which matched in every other "
                "quarter; the ratio is held out until the source is corrected"
                % (o["period"], ("%.2f" % o["vendor"]).rstrip("0").rstrip(".") + "%",
                   o["numerator"], o["denominator"],
                   ("%.2f" % o["parts"]).rstrip("0").rstrip(".") + "%"))
    if getattr(s, "erratic_stock", False):
        return "erratic_stock", ("Data-Quality Exception, Not Scored: a balance that alternates between two "
                "levels from quarter to quarter, which a stock of assets does not do; the "
                "series likely mixes two definitions")
    if getattr(s, "modelled_input", False):
        return "modelled_input", ("it is calculated from third-party estimates rather than reported "
                "figures, so it is not scored on a note built from reported data")
    if getattr(s, "shadow_kind", None) == "twin":
        return "shadow_twin", ("the same figures as %s over the last eight quarters, so the line is shown once"
                % s.shadowed_by)
    if getattr(s, "shadow_kind", None) == "no_adjustments":
        return "shadow_no_adjustments", ("InSync's ex-one-time-items EPS equals %s: no adjustments this year, so the "
                "company's diluted figure is shown" % s.shadowed_by)
    if getattr(s, "shadow_kind", None) == "company_adjusted":
        return "shadow_company_adjusted", "the company's own adjusted EPS (%s) is shown in its place" % s.shadowed_by
    if getattr(s, "shadow_kind", None) == "component":
        return "shadow_component", "the company's own contribution (%s) is shown in its place" % s.shadowed_by
    if getattr(s, "shadow_kind", None) == "basic":
        return "shadow_basic", "diluted EPS (%s) is shown in its place" % s.shadowed_by
    if getattr(s, "shadow_kind", None) == "basic_eps":
        return "shadow_basic", ("EPS is shown once, with %s shown in its place" % s.shadowed_by)
    if getattr(s, "shadowed_by", None):
        return "shadow_company_reported", ("the company reports this measure itself (%s); the company's figure is "
                "scored and this InSync calculation is shown beside it" % s.shadowed_by)
    if getattr(s, "spliced_with", None):
        return "spliced", ("the latest two quarters are the figures of %r, while the earlier history "
                "differs: the series changed what it measures" % s.spliced_with)
    return None, None


def _exclusion_reason_core(s):
    return _exclusion_core(s)[1]


def _exclusion_core(s):
    if "zero_read_as_missing" in (s.flags or []):
        return "zero_read_as_missing", ("the latest quarter came back as zero on a line that has never "
                "been near zero, which is a gap in the data rather than a "
                "reported figure")
    if s.modelled:
        return "modelled", "the figures are third-party estimates rather than reported actuals"
    if getattr(s, "stale", False):
        _u = getattr(s, "unreadable_by_label", None) or {}
        if getattr(s, "scan_quarter", None) in _u:
            return "latest_unreadable", ("the %s value came back unreadable (%r)"
                                         % (s.scan_quarter, _u[s.scan_quarter]))
        return "stale", "the company did not report it this quarter"
    _gap = _grid_gap_reason(s)
    if _gap:
        return _gap
    if getattr(s, "step_like", False):
        return "step_like", ("it moves in steps rather than continuously, so a rate of "
                             "change would describe the steps")
    if getattr(s, "cumulative", False):
        return "cumulative", ("the series reads as year-to-date rather than quarterly - it rises "
                "through every fiscal year and resets at the first quarter - so it "
                "cannot be compared with quarterly lines")
    if getattr(s, "unnamed", False):
        return "unnamed", ("the source's line-item name is a schedule bucket or code and does "
                "not say what is measured")
    if getattr(s, "count_as_money", False):
        return "count_as_money", ("it names a count (stores, restaurants, branches) but the "
                                  "source quotes it in money, so its unit is in conflict")
    if getattr(s, "unit_implausible", False):
        return "unit_implausible", ("it is a per-unit figure at a magnitude no per-unit figure has, so "
                "its scale could not be resolved")
    if s.obs < MIN_Q_FOR_RANK:
        return "short_history", "only %d quarters of history came back" % s.obs
    if "base_unstable" in (s.flags or []):
        return "base_unstable", ("the year-over-year history is broken: at least one year-ago "
                "quarter in the window is near zero or on the other side of zero")
    if getattr(s, "volatile_base", False):
        return "volatile_base", ("its own level swings too widely over the window for a growth "
                "rate to mean anything")
    if getattr(s, "repeats_year_ago", False):
        return "carried_forward", ("the latest figure repeats the year-ago figure to every "
                                   "digit while the quarters between moved, which reads as a "
                                   "carried-forward copy rather than a reported quarter")
    if s.cfo_component():
        return "cfo_bridge", ("a line of the operating cash flow bridge, not a line of the "
                              "business; the balance-sheet line is the reading")
    if s.z is None and s.latest_accel is not None:
        _hist = len([a for a in (s.accel or [])[:-1] if a is not None])
        return "short_accel_history", (
            "%d quarters of history came back, which leaves %d earlier changes in pace; "
            "scoring this one against its own past needs %d"
            % (s.obs, _hist, MIN_ACCEL_OBS_FOR_Z))
    return "unclassified", "held out: unclassified"


def _blank_implausible_zeros(s):
    vals = s.values
    zeros = [i for i, v in enumerate(vals) if v == 0]
    if not zeros:
        return
    if any(v is not None and v < 0 for v in vals) and any(v is not None and v > 0 for v in vals):
        _gap = [i for i in zeros if 0 < i < len(vals) - 1
                and vals[i - 1] is not None and vals[i + 1] is not None
                and abs(vals[i - 1]) >= 10 and abs(vals[i + 1]) >= 10
                and re.match(r"^%", (s.name or "").strip())]
        for i in _gap:
            vals[i] = None
        if _gap:
            s.flags.append("zero_read_as_missing")
            s.zeros_blanked = len(_gap)
        return
    nz = [abs(v) for v in vals if v is not None and v != 0]
    if len(nz) < 8 or len(zeros) > max(1, len(vals) // 10):
        return
    nz_sorted = sorted(nz)
    median = nz_sorted[len(nz_sorted) // 2]
    if not median or min(nz) / median < ZERO_IS_MISSING_FLOOR:
        return
    for i in zeros:
        vals[i] = None
    s.flags.append("zero_read_as_missing")
    s.zeros_blanked = len(zeros)


def _quarter_sort_key(label):
    m = re.fullmatch(r"([1-4])Q(\d{2})", str(label or "").strip().upper())
    return (int(m.group(2)), int(m.group(1))) if m else (-1, -1)


def _quarter_before(label, latest):
    m = re.fullmatch(r"([1-4])Q(\d{2})", str(label or "").strip().upper())
    n = re.fullmatch(r"([1-4])Q(\d{2})", str(latest or "").strip().upper())
    if not m or not n:
        return False
    return (int(m.group(2)), int(m.group(1))) < (int(n.group(2)), int(n.group(1)))


_DEFAULT_CUR = {"sym": None}


def _unit_str(v, lab, dp, bps_unit=False, div=None):
    if v is None:
        return None
    if lab == "%" or bps_unit:
        return f"{v:,.{dp if dp is not None else 2}f}%"
    if lab == "count":
        if abs(v) < 1e3 and float(v).is_integer():
            return "%d" % v
        return _money(v, "")
    if lab in (None, "currency"):
        return _money(v, _DEFAULT_CUR["sym"])
    if div:
        v = v / div
    if lab == "x":
        return f"{v:,.2f}x"
    return f"{v:,.{dp if dp is not None else 2}f} {lab}"


def _count_lab(s, lab):
    if lab is None and s is not None and SHARE_COUNT_PAT.search(str(getattr(s, "name", "") or "")):
        return "count"
    return lab


def _range_str(lo, hi, lab, dp, bps_unit=False, div=None):
    a = _unit_str(lo, lab, dp, bps_unit, div)
    if lo == hi:
        return a
    return a + " to " + _unit_str(hi, lab, dp, bps_unit, div)


def _swap_component_labels(raw):
    def _rng(t):
        t = str(t or "")
        if "%" not in t or re.search(r"[$\u20ac\u00a3]|\d\s*(?:m|mm|bn?|million|billion)\b", t, re.I):
            return None
        vals = []
        for m in re.finditer(r"(\()?\s*(-?\d+(?:\.\d+)?)\s*%\s*(\))?", t):
            v = float(m.group(2))
            if m.group(1) and m.group(3):
                v = -abs(v)
            vals.append(v)
        if not vals or len(vals) > 2:
            return None
        return (min(vals), max(vals))
    rows = {}
    for bucket in ("guidance", "outlook"):
        for g in (raw.get(bucket) or []):
            lab = str(g.get("LINEITEM") or g.get("lineitem") or "")
            m = re.match(r"^\s*(%\s*cc|%\s*fx|%\s*m&a|y/y)\s*-\s*(.+)$", lab, re.I)
            if not m:
                continue
            kind = re.sub(r"\s", "", m.group(1).lower())
            key = (re.sub(r"[^a-z0-9]", "", m.group(2).lower()),
                   str(g.get("FISCALPERIOD") or g.get("fiscal_period")),
                   str(g.get("SOURCE_PERIOD") or g.get("source_period")))
            txt = g.get("GUIDE_TEXT") if "GUIDE_TEXT" in g else g.get("guide_text")
            r = _rng(txt)
            if r is not None:
                rows.setdefault(key, {})[kind] = (r, g, lab)
    out = []
    for key, d in rows.items():
        if not all(k in d for k in ("y/y", "%cc", "%fx")):
            continue
        ma = d["%m&a"][0] if "%m&a" in d else (0.0, 0.0)
        tot, cc, fx = d["y/y"][0], d["%cc"][0], d["%fx"][0]
        as_filed = max(abs(cc[0] + fx[0] + ma[0] - tot[0]), abs(cc[1] + fx[1] + ma[1] - tot[1]))
        swapped = max(abs(tot[0] + fx[0] + ma[0] - cc[0]), abs(tot[1] + fx[1] + ma[1] - cc[1]))
        if as_filed >= 1.0 and swapped <= 0.5:
            gt, gc = d["y/y"][1], d["%cc"][1]
            ft = "GUIDE_TEXT" if "GUIDE_TEXT" in gt else "guide_text"
            fc = "GUIDE_TEXT" if "GUIDE_TEXT" in gc else "guide_text"
            t_old, c_old = gt.get(ft), gc.get(fc)
            gt[ft], gc[fc] = c_old, t_old
            for fld in ("LOW", "HIGH", "low", "high"):
                if fld in gt or fld in gc:
                    gt[fld], gc[fld] = gc.get(fld), gt.get(fld)
            out.append({"metric": d["y/y"][2].split("-", 1)[-1].strip(),
                        "target_period": key[1], "source_period": key[2],
                        "total_as_filed": str(t_old).strip(), "cc_as_filed": str(c_old).strip(),
                        "fx_text": str(d["%fx"][1].get("GUIDE_TEXT") or d["%fx"][1].get("guide_text") or "").strip(),
                        "ma_text": (str(d["%m&a"][1].get("GUIDE_TEXT") or d["%m&a"][1].get("guide_text") or "").strip()
                                    if "%m&a" in d else None)})
    return out


def _guidance(raw, series_by_code, latest_quarter):
    nxt = next_quarter(latest_quarter)
    scored, context, uncompared, delivered = [], [], [], []
    for g in raw.get("guidance", []) or []:
        code = (g.get("LTCODE") or g.get("ltcode") or "").strip()
        target = str(g.get("FISCALPERIOD") or g.get("fiscal_period") or "").strip()
        yoy_form = code.endswith("-Y")
        base_code = code[:-2] if yoy_form else code
        s = series_by_code.get(code) or series_by_code.get(base_code)
        if s is not None and getattr(s, "shadow_kind", None) in ("twin", "no_adjustments", "basic") \
                and series_by_code.get(getattr(s, "shadow_of_code", None)) is not None:
            s = series_by_code[s.shadow_of_code]
        lineitem = g.get("LINEITEM") or g.get("lineitem") or ""
        base_row = {
            "ltcode": code,
            "name": ((lineitem or code) if (yoy_form or not s) else s.name),
            "display": (display_name(lineitem or code) if (yoy_form or not s)
                        else (display_name(s.name) if getattr(s, "_label_proved", False) else s.display)),
            "guide_text": g.get("GUIDE_TEXT") or g.get("guide_text"),
            "guide_scale": str(g.get("SCALE") or g.get("scale") or ""),
            "target_period": target,
            "source_period": g.get("SOURCE_PERIOD") or g.get("source_period"),
            "guide_source": g.get("SOURCE") or g.get("source"),
            "yoy_form": yoy_form,
            "in_scan": s is not None,
            "resolved_ltcode": (s.ltcode if s is not None and s.ltcode != code
                                else None),
        }
        rng = parse_guide_range(g)
        base_row["guide_display_text"] = _guide_readable(g, rng, s, yoy_form)
        _gt = str(g.get("GUIDE_TEXT") or g.get("guide_text") or "")
        base_row["guide_kind"] = ("numeric" if rng is not None else
                                  "qualitative" if (QUALITATIVE_GUIDE_PAT.search(_gt)
                                                    or not re.search(r"\d", _gt))
                                  else "unparsed")
        base_row["guide_date"] = g.get("GUIDEDATE") or g.get("guide_date")
        if (rng is not None and not yoy_form and s is not None
                and re.fullmatch(r"(19|20)\d{2}", str(target or ""))
                and s.kind == "level" and s.latest_value
                and str(g.get("FORMAT_CODE") or g.get("format_code") or "").upper()[:1] != "P"):
            _mid = (rng[0] + rng[1]) / 2.0
            _q4 = 4.0 * abs(s.latest_value)
            try:
                if _mid and abs(math.log10(abs(_mid) / _q4)) > 1.0:
                    base_row["guide_display_text"] = None
                    base_row["scale_unresolved"] = True
            except (ValueError, ZeroDivisionError):
                pass
        if target == latest_quarter:
            row = _delivered_row(base_row, s, rng, yoy_form)
            (delivered if row.get("comparable") else uncompared).append(row)
            continue
        if target != nxt:
            context.append(base_row)
            continue
        if s is None:
            uncompared.append(dict(base_row, horizon="next_quarter",
                                   reason="line_not_in_scan"))
            continue
        if not s.rankable:
            uncompared.append(dict(base_row, horizon="next_quarter",
                                   reason="line_not_scoreable"))
            continue
        if rng is None:
            uncompared.append(dict(base_row, horizon="next_quarter",
                                   reason="qualitative_guide"))
            continue
        if yoy_form and not (s.kind == "level" and s.unit == "pct"):
            uncompared.append(dict(base_row, horizon="next_quarter",
                                   reason="growth_form_not_comparable"))
            continue
        mid = rng[0] if rng[0] == rng[1] else (rng[0] + rng[1]) / 2.0
        fb = s.forward_bar()
        row = dict(base_row, latest_growth=_guide_growth(s), latest_accel=s.latest_accel,
                   z=_clamp_display(s.z), unit=s.unit, guide_midpoint=mid,
                   guide_low=rng[0], guide_high=rng[1],
                   guide_is_range=(rng[0] != rng[1]),
                   guide_display_text=_range_str(
                       rng[0], rng[1], _count_lab(s, s.unit_info[0]), s.unit_info[2],
                       bps_unit=(s.unit == "bps"), div=s.unit_info[1]),
                   verdict="unparsed")
        if yoy_form:
            row["guide_is_growth_rate"] = True
            row["continuation_threshold"] = (fb or {}).get("level_to_hold_growth")
            row["guide_display_text"] = _range_str(rng[0], rng[1], "%",
                                                   s.unit_info[2])
        _rate_kpi = (not yoy_form and (s.kind == "pre_growth" or getattr(s, "pre_growth", False))
                     and str(g.get("FORMAT_CODE") or g.get("format_code") or "").upper()[:1] == "P")
        if _rate_kpi:
            row["guide_is_rate_reading"] = True
            row["guide_display_text"] = _range_str(rng[0], rng[1], "%", s.unit_info[2])
        if (yoy_form or _rate_kpi or (mid is not None and fb and fb.get("next_base_value"))):
            base = None if (yoy_form or _rate_kpi) else fb["next_base_value"]
            _neg = (not yoy_form and s.negative_convention and base is not None
                    and base < 0 and mid < 0 and s.kind != "ratio")
            implied = (mid if (yoy_form or _rate_kpi)
                       else ((mid - base) * 100.0 if s.kind == "ratio"
                             else (((abs(mid) - abs(base)) / abs(base) * 100.0) if _neg
                                   else ((mid - base) / abs(base) * 100.0 if base else None))))
            if implied is not None and s.latest_growth is not None:
                ga = implied - s.latest_growth
                row["guide_implied_growth"] = implied
                row["guide_implied_accel"] = ga
                row["continuation_threshold"] = (fb or {}).get("level_to_hold_growth")
                floor = (MIN_ABS_ACCEL_BPS if s.unit == "bps" else MIN_ABS_ACCEL_PP)
                turns_neg = (implied < 0 and (s.latest_growth or 0) > 0
                             if s.kind == "ratio" else
                             implied < 0 <= (s.latest_growth or 0))
                if turns_neg:
                    row["verdict"] = "turns negative"
                    row["verdict_display"] = (
                        "turns negative" if s.kind != "ratio" else
                        ("returns to y/y decrease" if s.lower_is_better
                         else "turns to y/y contraction"))
                elif abs(ga) < max(GUIDE_TOLERANCE_PP, floor):
                    row["verdict"] = "stabilises"
                elif (s.latest_growth or 0) < 0 <= implied:
                    row["verdict"] = "reverses to growth"
                    row["verdict_display"] = (
                        "reverses to growth" if s.kind != "ratio" else
                        ("returns to y/y increase" if s.lower_is_better
                         else "returns to y/y expansion"))
                elif s.kind == "ratio":
                    if ga > 0:
                        row["verdict"] = "continues acceleration"
                    else:
                        row["verdict"] = "continues at a slower rate"
                else:
                    if ga > 0:
                        row["verdict"] = "continues acceleration"
                    elif implied > 0:
                        row["verdict"] = "continues at a slower rate"
                    else:
                        row["verdict"] = "turns negative"
        scored.append(row)
    order = {"turns negative": 0, "reverses to growth": 1,
             "continues at a slower rate": 2, "stabilises": 3,
             "continues acceleration": 4, "not comparable": 5, "unparsed": 6}
    scored.sort(key=lambda r: (order.get(r["verdict"], 9),
                               -abs(r.get("guide_implied_accel") or 0.0),
                               r["ltcode"]))

    notes = []
    for o in (raw.get("outlook", []) or []):
        n = {
            "label": o.get("LINEITEM") or o.get("lineitem"),
            "text": o.get("GUIDE_TEXT") or o.get("guide_text"),
            "target_period": o.get("FISCALPERIOD") or o.get("fiscal_period"),
            "source_period": o.get("SOURCE_PERIOD") or o.get("source_period"),
        }
        n["targets_next"] = (str(n["target_period"] or "").strip() == nxt)
        n["targets_latest"] = (str(n["target_period"] or "").strip() == latest_quarter)
        n["declines"] = bool(DECLINES_PAT.search(str(n["text"] or "")))
        n.update(_note_match(n, series_by_code))
        notes.append(n)
    _latest_g, _gtexts = {}, []
    for g in raw.get("guidance", []) or []:
        _c = (g.get("LTCODE") or g.get("ltcode") or "").strip()
        _tp = str(g.get("FISCALPERIOD") or g.get("fiscal_period") or "").strip()
        _sp = str(g.get("SOURCE_PERIOD") or g.get("source_period") or "").strip()
        _tx = re.sub(r"\s+", "", str(g.get("GUIDE_TEXT") or g.get("guide_text") or "")).lower()
        _k = (_c, _tp)
        if _k not in _latest_g or _quarter_sort_key(_sp) > _quarter_sort_key(_latest_g[_k][0]):
            _latest_g[_k] = (_sp, _tx)
        _gtexts.append((_c, _tp, _sp, _tx))
    _superseded = {}
    for _c, _tp, _sp, _tx in _gtexts:
        _ls = _latest_g.get((_c, _tp))
        if _ls and _quarter_sort_key(_sp) < _quarter_sort_key(_ls[0]) and _tx and _tx != _ls[1]:
            _superseded[(_tx, _tp)] = _sp
    for n in notes:
        _nk = (re.sub(r"\s+", "", str(n.get("text") or "")).lower(),
               str(n.get("target_period") or "").strip())
        if _nk in _superseded and _quarter_sort_key(str(n.get("source_period") or "")) \
                <= _quarter_sort_key(_superseded[_nk]):
            n["superseded"] = True
    notes = [n for n in notes if not n.get("superseded")]
    notes.sort(key=lambda n: (0 if n["targets_next"] else
                              (1 if n["targets_latest"] else 2),
                              str(n.get("source_period") or ""),
                              str(n.get("label") or "")))
    seen_ctx, dedup_ctx = set(), []
    _stale = 0
    for r in sorted(context, key=lambda x: _quarter_sort_key(x.get("source_period")),
                    reverse=True):
        key = (r.get("ltcode"), r.get("target_period"))
        if key in seen_ctx:
            continue
        seen_ctx.add(key)
        if _quarter_before(r.get("target_period"), latest_quarter):
            _stale += 1
            continue
        dedup_ctx.append(r)
    _yr = ("20" + str(latest_quarter)[-2:]) if re.fullmatch(r"[1-4]Q\d\d", str(latest_quarter or "")) else None
    _recent = {str(latest_quarter)}
    try:
        _qq, _yy = int(str(latest_quarter)[0]), int(str(latest_quarter)[-2:])
        _recent.add(f"{_qq - 1}Q{_yy:02d}" if _qq > 1 else f"4Q{_yy - 1:02d}")
    except Exception:
        pass
    _HL = re.compile(r"earnings per share|\beps\b|revenue|net sales|margin|\bnoi\b|\bffo\b|"
                     r"medical (care|loss) ratio|operating (income|profit|earnings)", re.I)
    _seen_hl = set()
    for n in sorted(notes, key=lambda n: _quarter_sort_key(n.get("source_period")), reverse=True):
        if (_yr and str(n.get("target_period") or "").strip() == _yr
                and str(n.get("source_period") or "").strip() in _recent
                and not n.get("declines")
                and _HL.search(str(n.get("label") or "") + " " + str(n.get("text") or ""))
                and (len(re.findall(r"[A-Za-z]{2,}", str(n.get("text") or ""))) >= 5
                     or " - " not in str(n.get("label") or ""))):
            _k = re.sub(r"[^a-z]", "", str(n.get("label") or "").lower())
            _k = re.sub(r"\d+$", "", _k)
            if _k.startswith("guidenote"):
                _k = _k + re.sub(r"[^a-z]", "", str(n.get("text") or "").lower())[:40]
            if _k in _seen_hl:
                continue
            _seen_hl.add(_k)
            n["headline_annual"] = True
    def _pct_range(t):
        t = str(t or "")
        if "%" not in t or re.search(r"[$\u20ac\u00a3]|\d\s*(?:m|mm|bn?|million|billion)\b", t, re.I):
            return None
        vals = []
        for m in re.finditer(r"(\()?\s*(-?\d+(?:\.\d+)?)\s*%\s*(\))?", t):
            if not re.search(r"\d", m.group(0)):
                continue
            v = float(m.group(2))
            if m.group(1) and m.group(3):
                v = -abs(v)
            vals.append(v)
        if not vals or len(vals) > 2:
            return None
        return (min(vals), max(vals))
    def _g1(v):
        return ("%.1f" % v).rstrip("0").rstrip(".")
    component_checks = []
    _comp = {}
    for n in notes + [{"label": g.get("LINEITEM") or g.get("lineitem"),
                       "text": g.get("GUIDE_TEXT") or g.get("guide_text"),
                       "target_period": g.get("FISCALPERIOD") or g.get("fiscal_period"),
                       "source_period": g.get("SOURCE_PERIOD") or g.get("source_period")}
                      for g in (raw.get("guidance", []) or [])]:
        lab = str(n.get("label") or "")
        m = re.match(r"^\s*(%\s*cc|%\s*fx|%\s*m&a|y/y)\s*-\s*(.+)$", lab, re.I)
        if not m:
            continue
        kind = re.sub(r"\s", "", m.group(1).lower())
        base = re.sub(r"[^a-z0-9]", "", m.group(2).lower())
        key = (base, str(n.get("target_period")), str(n.get("source_period")))
        rng = _pct_range(n.get("text"))
        if rng is not None:
            _comp.setdefault(key, {})[kind] = (rng, str(n.get("text")).strip(), lab)
            _comp[key].setdefault("_base", m.group(2).strip())
    for key, d in _comp.items():
        if not all(k in d for k in ("y/y", "%cc", "%fx")) or str(key[2]) not in _recent:
            continue
        lo = d["%cc"][0][0] + d["%fx"][0][0] + (d["%m&a"][0][0] if "%m&a" in d else 0.0)
        hi = d["%cc"][0][1] + d["%fx"][0][1] + (d["%m&a"][0][1] if "%m&a" in d else 0.0)
        tlo, thi = d["y/y"][0]
        gap = max(abs(lo - tlo), abs(hi - thi))
        if gap >= 1.0:
            component_checks.append({
                "target_period": key[1], "source_period": key[2],
                "total_text": d["y/y"][1], "cc_text": d["%cc"][1], "fx_text": d["%fx"][1],
                "ma_text": d["%m&a"][1] if "%m&a" in d else None,
                "implied_display": (_g1(lo) + "% to " + _g1(hi) + "%") if lo != hi
                                   else _g1(lo) + "%",
                "gap_pp": round(gap, 1), "metric": d.get("_base")})
    fwd = [u for u in uncompared if u.get("horizon") == "next_quarter"]
    fwd.sort(key=lambda r: (r.get("display") or ""))
    dlv = [u for u in uncompared if u.get("horizon") == "reported_quarter"]
    delivered.sort(key=lambda r: (r.get("display") or ""))
    dlv.sort(key=lambda r: (r.get("display") or ""))
    rows_in = len(raw.get("guidance", []) or []) + len(raw.get("outlook", []) or [])
    return {"next_quarter": nxt, "scored": scored,
            "revisions": guidance_revisions(raw.get("guidance", []) or [],
                                            series_by_code, latest_quarter),
            "guide_history": guide_history(raw.get("guidance", []) or []),
            "forward_uncompared": fwd,
            "delivered": delivered, "delivered_uncompared": dlv,
            "other_horizon": dedup_ctx[:40], "superseded_quarterly_guides": _stale,
            "outlook_notes": notes[:8] + [n for n in notes[8:] if n.get("headline_annual")],
            "component_checks": component_checks,
            "declines_to_guide": sorted(
                (n for n in notes if n.get("declines")),
                key=lambda n: _quarter_sort_key(n.get("source_period")), reverse=True),
            "retrieval": {
                "rows_returned": rows_in,
                "guidance_rows": len(raw.get("guidance", []) or []),
                "outlook_rows": len(raw.get("outlook", []) or []),
                "targets_next_quarter": (len(scored) + len(fwd)
                                         + sum(1 for n in notes if n["targets_next"])),
                "state": ("not_retrieved" if rows_in == 0 else
                          ("declines_to_guide"
                           if (any(n.get("declines") for n in notes)
                               and not (scored or fwd
                                        or any(n["targets_next"] and not n.get("declines")
                                               for n in notes))) else
                           ("none_for_next_quarter"
                            if not (scored or fwd or any(n["targets_next"] for n in notes))
                            else "retrieved"))),
            }}


DECLINES_PAT = re.compile(
    r"\b(?:not|no longer|ceased?|stopped)\b[^.]{0,30}"
    r"\b(?:provid|issu|giv|offer)\w*[^.]{0,30}\bguidance\b"
    r"|\b(?:suspend(?:ed|ing|s)?|withdr(?:ew|awn|awing|aws)|discontinu\w*)\b"
    r"[^.]{0,40}\bguidance\b"
    r"|\bguidance\b[^.]{0,40}"
    r"\b(?:suspended|withdrawn|discontinued|not being (?:provided|given|issued))\b",
    re.I)


NOTE_STOP = {"the", "a", "an", "of", "for", "in", "on", "to", "and", "or", "at",
             "per", "with", "is", "be", "expects", "expected", "approximately",
             "about", "roughly", "company", "quarter", "year", "over", "up",
             "down", "growth", "guide", "note", "adjusted", "consolidated",
             "total", "average", "ex", "items", "1x", "co", "defined"}


def _note_words(text):
    return {w for w in re.findall(r"[a-z]+", (text or "").lower())
            if len(w) > 2 and w not in NOTE_STOP}


def _note_match(note, series_by_code):
    text = note.get("text") or ""
    toks = [m.group(0).strip() for m in GUIDE_NUM_PAT.finditer(text.replace("$", " "))]
    toks = [t for t in toks if re.search(r"\d", t)]
    if len(toks) != 1:
        return {}
    if re.search(r"(\u00b1|\+/-|\+ ?/ ?-|plus or minus)\s*" + re.escape(toks[0].strip()), text) \
            or re.search(re.escape(toks[0].strip()) + r"\s*bps?\b", text, re.I):
        return {}
    val, _ = parse_value(toks[0])
    if val is None:
        return {}
    words = _note_words(text) | _note_words(note.get("label"))
    uniq = {s.ltcode: s for s in series_by_code.values()}
    best, best_n = [], 0
    for _c, s in sorted(uniq.items()):
        n = len(words & _note_words(s.name))
        if n >= 2 and n > best_n:
            best, best_n = [s], n
        elif n >= 2 and n == best_n:
            best.append(s)
    if len(best) != 1:
        return {}
    s = best[0]
    lvl, lab, dp, status = s.display_level
    lab = _count_lab(s, lab)
    if lvl is None or not lvl or status == "conflicted":
        return {}
    try:
        if abs(math.log10(abs(val) / abs(lvl))) > 1.0:
            return {}
    except (ValueError, ZeroDivisionError):
        return {}
    gap = (lvl - val) if note.get("targets_latest") else None
    return {"matched_ltcode": s.ltcode, "matched_display": s.display,
            "note_value": val, "note_unit": lab, "note_decimals": dp,
            "matched_level": lvl,
            "note_value_display": _unit_str(val, lab, dp),
            "matched_level_display": _unit_str(lvl, lab, dp),
            "matched_gap": gap,
            "matched_gap_display": (None if gap is None else
                                    _unit_str(abs(gap), lab, dp)
                                    + (" below the guide" if gap < 0
                                       else " above the guide"))}


def guide_position(act, lo, hi, tol=0.0, magnitude=False):
    if magnitude:
        act, lo, hi = -act, -hi, -lo
    if act > hi + tol:
        return "above"
    if act < lo - tol:
        return "below"
    return "within"


def _delivered_row(base_row, s, rng, yoy_form):
    row = dict(base_row, horizon="reported_quarter", comparable=False)
    if s is None:
        row["reason"] = "line_not_in_scan"
        return row
    if rng is None:
        row["reason"] = "qualitative_guide"
        return row
    if yoy_form:
        row["reason"] = "growth_form_not_comparable"
        return row
    lvl, lab, dp, status = s.display_level
    lab = _count_lab(s, lab)
    if lvl is None or status == "conflicted":
        row["reason"] = "level_withheld"
        return row
    if s.kind == "level" and getattr(s, "provenance", None) == "derived":
        row["reason"] = "actual_is_calculated"
        return row
    lo, hi = rng
    _div = (s.unit_info[1] if lab not in (None, "currency", "%") else None)
    if _div:
        lo, hi = lo / _div, hi / _div
    mid = lo if lo == hi else (lo + hi) / 2.0
    try:
        if mid and abs(math.log10(abs(mid) / abs(lvl))) > 1.0:
            row["reason"] = "scale_mismatch"
            return row
    except (ValueError, ZeroDivisionError):
        row["reason"] = "scale_mismatch"
        return row
    _mag = bool(getattr(s, "negative_convention", False) and lvl < 0 and hi <= 0)
    _sign_flip = False
    if not _mag and lvl and hi <= 0 and lo < 0 and lvl > 0:
        lo, hi = -hi, -lo
        mid = lo if lo == hi else (lo + hi) / 2.0
        _sign_flip = True
    _tol = 0.0
    _gt = str(base_row.get("guide_text") or "")
    _num = re.search(r"\d[\d,]*(?:\.(\d+))?", _gt)
    if lo == hi and _num and not _div:
        try:
            _written = float(_num.group(0).replace(",", ""))
            if _written:
                _tol = 0.5 * 10 ** (-len(_num.group(1) or "")) * abs(lo) / _written
        except (ValueError, OverflowError):
            _tol = 0.0
    pos = guide_position(lvl, lo, hi, _tol, _mag)
    row.update({
        "comparable": True, "guide_low": lo, "guide_high": hi,
        "guide_midpoint": mid, "guide_is_range": (lo != hi),
        "actual": lvl, "actual_unit": lab, "actual_decimals": dp,
        "position": pos, "precision_tolerance": _tol, "compare_on_magnitude": _mag or _sign_flip,
        "sign_convention_differs": _sign_flip,
        "gap": (lvl - hi if pos == "above" else (lvl - lo if pos == "below" else 0.0)),
        "unit": s.unit, "kind": s.kind,
        "latest_growth": s.latest_growth,
    })
    _bound = hi if pos == "above" else lo
    gap_v, gap_unit, gap_text = format_gap(row["gap"], s.unit, s.kind, s.name, lab, dp, _bound)
    row["gap_display_value"] = gap_v
    row["gap_unit"] = gap_unit
    row["gap_display"] = gap_text

    bps = (s.unit == "bps")
    row["guide_display_text"] = _range_str(lo, hi, lab, dp, bps_unit=bps)
    _pm = re.search(r"\+/-\s*([\d.]+)\s*(%?)", str(row.get("guide_text") or ""))
    if _pm and lo == hi:
        row["guide_display_text"] += " \u00b1 " + _pm.group(1) + _pm.group(2)
    row["actual_display"] = _unit_str(lvl, lab, dp, bps_unit=bps)
    if pos == "within":
        row["versus_display"] = "within the guided range"
    else:
        edge = (("the top of the range" if pos == "above" else "the bottom of the range")
                if lo != hi else "the guided figure")
        row["versus_display"] = f"{'above' if pos == 'above' else 'below'} {edge} by {gap_text}"
    return row


def gap_unit_family(unit, kind, name):
    if kind == "pre_growth" or (unit == "bps" and GROWTH_RATE_NAME_PAT.search(name or "")):
        return "rate"
    if unit == "bps" or kind == "ratio":
        return "ratio"
    return "level"


def format_gap(gap, unit, kind, name, lab, dp, bound):
    fam = gap_unit_family(unit, kind, name)
    if fam == "rate":
        v = gap
        return v, "pp", "%s pp" % _num_fmt(abs(v), 1)
    if fam == "ratio":
        v = gap * 100.0
        return v, "bps", "%s bps" % _num_fmt(abs(v), 0)
    if bound:
        v = gap / abs(bound) * 100.0
        return v, "%", "%s%%" % _num_fmt(abs(v), 1)
    return gap, (lab or "currency"), _unit_str(abs(gap), lab, dp)


def _num_fmt(v, dp):
    t = f"{v:,.{dp}f}"
    return "0" + ("." + "0" * dp if dp else "") if re.fullmatch(r"-0(\.0+)?", t) else t


DEEP_DIVE_FAMILIES = [
    ("ffo", r"\bffo\b|\baffo\b|funds from operations"),
    ("occupancy", r"occupan"),
    ("rent_change", r"rent change"),
    ("fee_revenue", r"base fee|advisory fee|investment advisory|"
                    r"administration fees|performance fee"),
    ("earnings", r"\beps\b|earnings per share|net income|\bpbt\b|pre-?tax|"
                 r"net margin|profit before tax"),
    ("operating_profit", r"\bebit\b|\bebitda\b|operating income|operating margin"),
    ("revenue", r"revenue|net sales|\bsales\b|billings|bookings"),
    ("gross_margin", r"gross margin|gross profit|\bgm\b|\bcogs\b"),
    ("cash", r"cash flow|\bfcf\b|\bocf\b|cash and equivalent|cash/share"),
    ("working_capital", r"inventor|receivab|payabl|days of|prepaid|deferred revenue"),
    ("opex", r"\bopex\b|\br&d\b|\bsg&a\b|\bs&m\b|restructur|compensation"),
    ("balance_sheet", r"\bdebt\b|lease|goodwill|intangible|equity|total assets"),
    ("obligations", r"remaining performance|backlog|\brpo\b"),
    ("spread_income", r"net interest income|\bnii\b|net interest margin|\bnim\b|"
                      r"interest income|interest expense|earning assets|loan yield"),
    ("credit", r"provision|charge-?off|\bnco\b|nonaccrual|non-?performing|"
               r"delinquen|reserve|allowance|criticized|impair"),
    ("capital", r"\bcet\s?1\b|tier\s?1\b|tier\s?2\b|\brwa\b|risk-?weighted|"
                r"capital ratio|leverage ratio|book value per share|tangible common"),
    ("balances", r"\bloans?\b|\bdeposits?\b|securities|lending related|"
                 r"assets under|\baum\b|client assets"),
    ("fee_income", r"fee income|investment banking|advisory|underwriting|"
                   r"asset management fee|card income|service charges"),
    ("cat_losses", r"catastrophe"),
    ("reserve_development", r"prior period development|prior year development|"
                            r"reserve development"),
    ("combined_ratio", r"combined ratio|underwriting (income|profit|margin)"),
    ("loss_ratio", r"loss ratio|medical (care|cost|loss) ratio|\bmlr\b|\bmcr\b"),
    ("expense_ratio", r"expense ratio"),
    ("premium", r"premium"),
    ("policy_count", r"policies in force|policy count|policies-in-force|"
                     r"\bpif\b|retention rate"),
    ("unit_cost", r"cost\s*/|cost per|\bcasm\b|lifting cost|per unit cost|"
                  r"opex\s*/|expense\s*/"),
    ("unit_revenue", r"\btrasm\b|\bprasm\b|passenger mile yield|yield\s*/\s*rpm|"
                     r"revenue per available seat|revenue per (passenger|seat) mile|"
                     r"\brasm\b|\barpu\b|revenue per (user|subscriber|customer)"),
    ("volumes", r"production|barrels|\bboe\b|throughput|reserves|rig count|"
                r"units shipped|shipments|prescriptions|procedures|scripts"),
    ("realisation", r"realized price|realisation|netback|crack spread|refining margin|"
                    r"average selling price|\basp\b|ticket|basket"),
]
MAX_PER_FAMILY = 2
_NET_MARGIN_PAT = re.compile(r"net margin|margin\W+net income|net income margin", re.I)
_EPS_NAME_PAT = re.compile(r"\beps\b|earnings per share", re.I)
_ADJ_BASIS_PAT = re.compile(r"ex-?\s?1x|adjusted|non-?gaap|excl", re.I)
MAX_CONTEXT_IN_TOP = 2
DIVE_MAX_PER_FAMILY = 1


def _per_unit_prefix(row):
    if row is None:
        return ""
    if isinstance(row, dict):
        if row.get("identity_unit") or row.get("flow_rate_mode"):
            return "perunit:"
        lab = str(row.get("unit_label") or "")
    else:
        if getattr(row, "identity_unit", None) or getattr(row, "flow_rate_mode", None):
            return "perunit:"
        try:
            lab = str(row.unit_info[0] or "")
        except Exception:
            return ""
    return "perunit:" if "/" in lab else ""


def _family(name, row=None):
    n = BASIS_TOKEN_PAT.sub(" ", (name or "").lower())
    if re.match(r"^\(\s*.+?\s+-\s+.+?\)\s*/\s*(total\s+|net\s+)?(revenue|sales)\b", n):
        return _per_unit_prefix(row) + "operating_profit"
    for key, pat in DEEP_DIVE_FAMILIES:
        if re.search(pat, n):
            return _per_unit_prefix(row) + key
    core = re.split(r"\s*[-\u2013(]\s*", n)[0]
    words = [w for w in re.findall(r"[a-z0-9&]+", core)
             if w not in ("total", "net", "the", "and", "of", "per",
                          "consolidated", "adjusted", "average", "composite")]
    base = "other:" + " ".join(words[:2]) if words else "other:" + n[:16]
    return _per_unit_prefix(row) + base


NON_COMPARABLE_PENALTY = 0.5
TEMPLATE_SLOT_BONUS = 0.5
NON_COMPARABLE_PAT = re.compile(r"not comparable|gross rather than a net|"
                                r"pro\s*-?forma|not on a like-for-like", re.I)


PRIMARY_SUPPORTING_FLOOR = 0.70


def _scan_status(raw, series, quarters):
    planned = raw.get("planned_codes") or []
    fetched = {(r.get("LTCODE") or r.get("ltcode"))
               for r in (raw.get("actuals") or [])}
    absent = set(raw.get("codes_absent_upstream") or [])
    ps_planned = ps_got = 0
    for item in (planned if isinstance(planned, list) else []):
        if not isinstance(item, dict):
            continue
        code = item.get("ltcode")
        if code in absent:
            continue
        nm, tags = item.get("name"), item.get("tags")
        t = tier_of(nm, tags or [])
        if t in (1, 2):
            ps_planned += 1
            if code in fetched:
                ps_got += 1
    pct = (ps_got / ps_planned) if ps_planned else None
    conflicted_lines = sorted(display_name(s.name) for s in series
                              if (s.unit_info or [None, None, None, None])[3] == "conflicted")
    conflicted = len(conflicted_lines)
    short = (pct is not None and pct < PRIMARY_SUPPORTING_FLOOR)
    if short and conflicted:
        grade = "Low"
    elif short or conflicted or (quarters or 0) < LOOKBACK_QUARTERS:
        grade = "Moderate"
    else:
        grade = "High"
    return {
        "primary_supporting_planned": ps_planned or None,
        "primary_supporting_retrieved": ps_got or None,
        "primary_supporting_pct": (round(pct, 3) if pct is not None else None),
        "floor": PRIMARY_SUPPORTING_FLOOR,
        "partial": bool(short) if pct is not None else None,
        "unresolved_units": conflicted,
        "unresolved_unit_lines": conflicted_lines[:6],
        "quantised_lines": [],
        "unresolved_units_in_counts": None,
        "scan_confidence": grade,
        "basis": ("share of the DISCOVERED primary and supporting lines that this "
                  "run actually read, against a %d%% floor; unresolved units and a "
                  "short lookback each cap the grade"
                  % int(PRIMARY_SUPPORTING_FLOOR * 100)),
    }


def slot_priority(r):
    pri = r.get("priority") or 0.0
    if NON_COMPARABLE_PAT.search(str(r.get("basis_qualifier") or "")):
        pri -= NON_COMPARABLE_PENALTY
    tr = (r.get("priority_components") or {}).get("template_rank")
    if tr is not None:
        n = max(len(TEMPLATE_PRIORITY.get(r.get("template") or "") or [1]), 1)
        pri += TEMPLATE_SLOT_BONUS * (1.0 - min(int(tr), n) / float(n))
    return max(pri, r.get("inherited_slot_priority") or 0.0)


def _drop_redundant_derived(rows):
    by_name = {(r.get("name") or "").strip().lower(): r for r in rows}
    keep, promote = [], {}
    for r in rows:
        d = r.get("derived_from") if isinstance(r, dict) else None
        if d:
            parts = [(x or "").strip().lower() for x in d.get("parts", [])]
            if len(parts) == 2 and all(x in by_name for x in parts):
                r["yielded_to_parts"] = d["parts"]
                for x in parts:
                    promote[x] = max(promote.get(x, 0.0), r.get("priority") or 0.0)
                continue
        keep.append(r)
    for nm, pri in promote.items():
        tgt = by_name.get(nm)
        if tgt is not None:
            tgt["inherited_slot_priority"] = max(
                tgt.get("inherited_slot_priority") or 0.0, pri)
            tgt["slot_priority_note"] = "inherited from the composite it replaces"
    return keep or rows


def _ebitda_off_template(r):
    t = r.get("template")
    if t == "reit" and re.search(r"\bebitda\b", r.get("name") or "", re.I):
        return True
    if not t or r.get("provenance") != "derived":
        return False
    if not re.search(r"\bebitda\b", r.get("name") or "", re.I):
        return False
    return not any(re.search(r"ebitda", p.pattern, re.I)
                   for p in (TEMPLATE_PRIORITY.get(t) or []))


def front_page_eligible(r):
    if "quantised_source" in (r.get("flags") or []) and r.get("implication") == "Ambiguous":
        return False
    if re.search(r"tax rate|\betr\b", r.get("name") or "", re.I):
        return False
    if re.search(r"\bcapex\b|capital expend", r.get("name") or "", re.I) \
            and not re.search(r"guid", r.get("name") or "", re.I):
        return False
    if re.search(r"payout ratio|stock[- ]based comp|\bsbc\b", r.get("name") or "", re.I) \
            or (re.search(r"book to bill", r.get("name") or "", re.I)
                and r.get("provenance") == "derived"):
        return False
    if r.get("period_basis") in ("unverified", "year_to_date"):
        return False
    if re.search(r"financing activit|\bcff\b|interest expense|"
                 r"interest (and other )?income\s*/\s*expense", r.get("name") or "", re.I):
        return False
    if (r.get("tier") or 3) <= 2 and not _ebitda_off_template(r):
        return True
    code = str(r.get("ltcode") or "")
    if code.startswith("BS_") and template_rank(r.get("template"), r.get("name")) is None \
            or re.search(r"financing activit|\bcff\b|interest expense|"
                                            r"interest (and other )?income\s*/\s*expense",
                                            r.get("name") or "", re.I):
        return False
    if _ebitda_off_template(r):
        return False
    if r.get("implication") in ("Favourable", "Unfavourable"):
        return True
    why = str(r.get("implication_reason") or "")
    return bool(why) and why != "direction is context dependent for this line" \
        and not why.startswith("no measurable change")


def _row_template_rank(r):
    if (r.get("segment_revenue_share") or 0) >= SEGMENT_SLOT_SHARE:
        return _template_revenue_slot(r.get("template"))
    if r.get("segment_scoped") or str(r.get("ltcode") or "").startswith("SD_") \
            or SEGMENT_DETAIL_PAT.search(r.get("name") or ""):
        return None
    return template_rank(r.get("template"), r.get("name"))


def _template_revenue_slot(template):
    pats = TEMPLATE_PRIORITY.get(template or "") or []
    hits = [i for i, p in enumerate(pats) if re.search(r"revenue|sales", p.pattern, re.I)]
    return hits[1] if len(hits) > 1 else None


def _family_representatives(rows):
    best = {}
    for r in rows:
        fam = _family(r["name"], r)
        tr = _row_template_rank(r)
        key = ((r.get("tier") or 3), tr if tr is not None else 99,
               1 if re.search(r"\bbasic\b", r.get("name") or "", re.I) else 0,
               -(r.get("slot_priority") or r.get("priority") or 0.0))
        if fam not in best or key < best[fam][0]:
            best[fam] = (key, r)
    keep = {id(v[1]) for v in best.values()}
    ranked_fams = {fam for fam, (key, _) in best.items() if key[1] != 99}
    return [r for r in rows if _family(r["name"], r) not in ranked_fams or id(r) in keep]


RATIO_NOUNS = [
    (r"\bsss\b|comparable (store )?sales|same[- ]store sales|\bcomps?\b", "comparable-sales growth"),
    (r"occupan", "occupancy"),
    (r"rent change", "rent change"),
    (r"retention", "retention"),
    (r"load factor", "load factor"),
    (r"payout", "payout ratio"),
    (r"tax rate|\betr\b", "tax rate"),
    (r"growth\b|y/y|yoy|\bcagr\b|organic|\bcc\b|constant currency", "growth rate"),
    (r"price\s*/?\s*mix|contribution|\bfx\b|currency|m&a", "contribution"),
    (r"take rate|yield", "yield"),
    (r"coverage", "coverage"),
    (r"combined ratio|loss ratio|care ratio|expense ratio|operating ratio|"
     r"efficiency ratio|\bmlr\b|\bmcr\b|ratio", "ratio"),
    (r"margin|\bgm\b", "margin"),
    (r"%\s*revenue|as % of|share of|% of", "share"),
]


def ratio_noun(name):
    n = (name or "").lower()
    for pat, noun in RATIO_NOUNS:
        if re.search(pat, n):
            return noun
    return None


def _pick_deep_dives(rows):
    rows = _drop_redundant_derived(rows)
    rows = [r for r in rows if front_page_eligible(r)] or rows
    for r in rows:
        r["slot_priority"] = slot_priority(r)
    rows = _family_representatives(rows)
    ranked = sorted(rows, key=lambda r: (-r["slot_priority"], -abs(r["z"]),
                                         r["ltcode"] or ""))
    picked, used = [], {}
    _ratios = 0
    for r in ranked:
        fam = _family(r["name"], r)
        if used.get(fam, 0) >= DIVE_MAX_PER_FAMILY:
            continue
        if r.get("kind") == "ratio" and _ratios >= DIVE_MAX_RATIOS:
            continue
        used[fam] = used.get(fam, 0) + 1
        _ratios += 1 if r.get("kind") == "ratio" else 0
        r["family"] = fam
        picked.append(r)
        if len(picked) == DEEP_DIVE_N:
            return picked
    for r in ranked:
        if len(picked) == DEEP_DIVE_N:
            break
        fam = _family(r["name"], r)
        if (r not in picked and used.get(fam, 0) < DIVE_MAX_PER_FAMILY
                and not (r.get("kind") == "ratio" and _ratios >= DIVE_MAX_RATIOS)):
            used[fam] = used.get(fam, 0) + 1
            _ratios += 1 if r.get("kind") == "ratio" else 0
            r["family"] = fam
            picked.append(r)
    picked.sort(key=lambda r: (-(r.get("priority") or 0.0), -abs(r["z"]),
                               r["ltcode"] or ""))
    return picked


CASH_PANEL = [
    ("Accounts Receivable", r"accounts receivable|^(trade )?receivables?\b(?!.*finance)"),
    ("Inventory", r"^inventor(y|ies)\b"),
    ("Inventory Days", r"days of inventory"),
    ("Deferred Revenue", r"total deferred revenue|^deferred revenue"),
    ("Operating Cash Flow", r"^ocf\b|operating cash flow"),
    ("Free Cash Flow", r"^fcf\b|^free cash flow"),
    ("Capital Expenditure", r"less\s*:\s*capex|capital expenditure"),
]
CASH_PANEL_CODES = {"Operating Cash Flow": "CF_P_108", "Free Cash Flow": "CF_P_110",
                    "Capital Expenditure": "CF_P_109",
                    "Accounts Receivable": "BS_P_119", "Inventory": "BS_P_147",
                    "Deferred Revenue": "BS_P_105"}


CASH_PANEL_READS = ("outgrowing revenue", "growing faster than revenue", "broadly tracking revenue",
                    "growing more slowly than revenue", "fell while revenue grew",
                    "fell faster than revenue", "fell while revenue also fell",
                    "reported")


def _panel_read(gap, growth, rev_growth):
    if gap is None or growth is None:
        return "reported"
    if growth < 0:
        if (rev_growth or 0) < 0:
            return ("fell faster than revenue" if gap < 0
                    else "fell while revenue also fell")
        return "fell while revenue grew"
    if gap > 10.0:
        return "outgrowing revenue"
    if gap > 5.0:
        return "growing faster than revenue"
    if gap >= -5.0:
        return "broadly tracking revenue"
    return "growing more slowly than revenue"


def _cash_conversion(series, rev, profit_accelerating=False):
    if rev is None or rev.latest_accel is None or (rev.latest_accel <= 0
                                                   and not profit_accelerating):
        return {"triggered": False,
                "reason": ("neither revenue nor earnings accelerated this quarter"
                           if rev is not None else
                           "revenue did not accelerate this quarter")}
    rows, missing, held_out = [], [], []
    spliced_notes = []
    for label, pat in CASH_PANEL:
        _code = CASH_PANEL_CODES.get(label)
        _own = None
        if label == "Free Cash Flow":
            _own = next((x for x in series if re.search(
                r"free cash flows? \(non-?gaap\)|co\.?\s*defined fcf|company[- ]defined (fcf|free cash)",
                x.name or "", re.I) and x.latest_growth is not None
                and x.provenance in ("filed", "presented")), None)
        s = (_own or next((x for x in series if _code and x.ltcode == _code
                   and x.latest_growth is not None), None)
             or next((x for x in series if re.search(pat, x.name or "", re.I)
                      and x.latest_growth is not None), None))
        if s is None:
            _any = next((x for x in series if (_code and x.ltcode == _code)
                         or re.search(pat, x.name or "", re.I)), None)
            if _any is not None:
                held_out.append(label)
                continue
            missing.append(label)
            continue
        _sv = [v for v in (s.values or []) if v is not None]
        _flips = sum(1 for a, b in zip(_sv, _sv[1:]) if (a > 0) != (b > 0))
        if _flips > 2:
            missing.append(label)
            held_out.append(label)
            continue
        if getattr(s, "spliced_with", None):
            held_out.append(label)
            spliced_notes.append({"label": label, "with": s.spliced_with})
            continue
        _days = bool(re.search(r"\bdays\b", label, re.I))
        gap = (s.latest_growth - rev.latest_growth) if (s.kind == "level" and not _days) else None
        _lvl = s.display_level
        rows.append({
            "label": label, "display": s.display, "ltcode": s.ltcode,
            "unit": s.unit, "latest_growth": s.latest_growth,
            "level": (_lvl[0] if _lvl else None),
            "level_unit": (_lvl[1] if _lvl else None),
            "level_display": (_money(_lvl[0], getattr(s, "currency", None))
                              if _lvl and _lvl[0] is not None else None),
            "prior_level": s.prior_year_display,
            "prior_level_display": (_money(s.prior_year_display, getattr(s, "currency", None))
                                    if s.prior_year_display is not None else None),
            "gap_versus_revenue": gap,
            "status": (("longer than a year ago" if (s.latest_growth or 0) > 0
                        else "shorter than a year ago") if _days
                       else _panel_read(gap, s.latest_growth, rev.latest_growth)),
            "period_basis": (s.period_basis()[0] if s._is_flow() else None),
            "period_evidence": (s.period_basis()[1] if s._is_flow() else None),
        })
    flagged = [r for r in rows if r.get("status") == "outgrowing revenue"]
    _deferred = [r for r in flagged if r["label"] == "Deferred Revenue"]
    flagged = [r for r in flagged if r["label"] != "Deferred Revenue"]
    _def_txt = ("" if not _deferred else
                " Deferred Revenue grew %+.1f%%, which is billing collected ahead "
                "of revenue rather than revenue left uncollected."
                % _deferred[0]["latest_growth"])
    _held_cash = [x for x in held_out if x in ("Operating Cash Flow", "Free Cash Flow")]
    _held_txt = (" and ".join(_held_cash)
                 + (" was" if len(_held_cash) == 1 else " were")
                 + " returned but not compared: the year-over-year history is "
                   "broken, with year-ago quarters near zero or on the other side "
                   "of zero") if _held_cash else ""
    cash = next((r for r in rows if r["label"] in ("Free Cash Flow", "Operating Cash Flow")
                 and r.get("latest_growth") is not None), None)
    parts = []
    for r in flagged:
        parts.append(f"{r['label']} grew {r['latest_growth']:+,.1f}%")
    rev_txt = f"revenue growth of {rev.latest_growth:+,.1f}%"
    _CASH_LABELS = ("Operating Cash Flow", "Free Cash Flow")
    cash_flagged = [r for r in flagged if r["label"] in _CASH_LABELS]
    if parts and cash_flagged:
        parts = ["%s grew %+.1f%%" % (r["label"], r["latest_growth"]) for r in cash_flagged]
        _moved = [r for r in rows
                  if r["label"] not in _CASH_LABELS
                  and r.get("latest_growth") is not None
                  and abs(r["latest_growth"]) >= 5.0]
        _mech = ("; over the same period "
                 + ", ".join("%s %s%.1f%%" % (r["label"].lower(),
                                              "+" if r["latest_growth"] > 0 else "",
                                              r["latest_growth"]) for r in _moved[:3])
                 if _moved else "")
        conclusion = (", ".join(parts) + f", compared with {rev_txt}" + _mech + ".")
    elif parts and cash:
        conclusion = (", ".join(parts) + f", compared with {rev_txt}, while "
                      f"{cash['label']} changed {cash['latest_growth']:+,.1f}%.")
    elif parts:
        conclusion = (", ".join(parts) + f", compared with {rev_txt}. "
                      + ((_held_txt[:1].upper() + _held_txt[1:]) if _held_txt
                         else "Cash flow was not returned by this scan")
                      + ", so the conversion question is open.")
    else:
        named = [r for r in rows
                 if r["label"] in ("Operating Cash Flow", "Free Cash Flow")
                 and r.get("latest_growth") is not None]
        if named:
            facts = ", ".join(
                f"{r['label']} {r['latest_growth']:+,.1f}%"
                + (f" at {r['level_display']}" if r.get("level_display") else "")
                for r in named)
            behind = [r for r in named
                      if r["latest_growth"] < rev.latest_growth - 10.0]
            fell = [r for r in behind if r["latest_growth"] < 0]
            slower = [r for r in behind if r["latest_growth"] >= 0]
            if fell and slower:
                tail = (f" {len(fell)} of {len(named)} cash lines fell outright and "
                        f"{len(slower)} grew more slowly than revenue.")
            elif fell:
                tail = (f" {'Both' if len(fell) == len(named) == 2 else str(len(fell))}"
                        f" cash line{'' if len(fell) == 1 else 's'} fell outright"
                        + ("." if len(fell) == len(named)
                           else f", of {len(named)} shown."))
            elif slower:
                tail = (f" Cash generation grew more slowly than revenue on "
                        f"{len(slower)} of {len(named)} cash lines.")
            else:
                tail = (" No panel line outgrew revenue by more than the "
                        "reporting threshold.")
            conclusion = (f"Revenue {'grew' if rev.latest_growth >= 0 else 'changed'} {rev.latest_growth:+,.1f}% against " + facts
                          + "." + tail)
        elif _held_txt:
            conclusion = (f"Revenue {'grew' if rev.latest_growth >= 0 else 'changed'} {rev.latest_growth:+,.1f}%; " + _held_txt
                          + ", so the conversion question is open.")
        else:
            conclusion = (f"Revenue {'grew' if rev.latest_growth >= 0 else 'changed'} {rev.latest_growth:+,.1f}% and no cash line was "
                          "returned by this scan, so the conversion question is open.")
    conclusion = conclusion + _def_txt
    _CASH = ("Operating Cash Flow", "Free Cash Flow")
    _WC = re.compile(r"receivable|inventor|unbilled|contract asset", re.I)
    _wc_up = [r for r in flagged if _WC.search(r["label"])]
    _cash_kept_pace = any(r["label"] in _CASH and r.get("latest_growth") is not None
                          and r["latest_growth"] >= rev.latest_growth for r in rows)
    quality = None
    if _wc_up and not _cash_kept_pace and not any(
            r["label"] in conclusion for r in _wc_up):
        quality = (", ".join("%s grew %+.1f%%" % (r["label"], r["latest_growth"])
                             for r in _wc_up[:2])
                   + " against revenue growth of %+.1f%%, while no cash line kept pace."
                   % rev.latest_growth)
    return {"triggered": True, "revenue_growth": rev.latest_growth,
            "revenue_accel": rev.latest_accel, "rows": rows,
            "not_disclosed": [m for m in missing if m not in held_out],
            "conclusion": conclusion + "".join(
                " %s is held out: the latest two quarters are the figures of %s, so the "
                "series changed what it measures." % (n["label"], n["with"])
                for n in spliced_notes),
            "quality_note": quality, "spliced": spliced_notes}


_STRIP_EPS_PATS = (r"^diluted eps \(ex-?\s?1x(?!.*\(excl)", r"^adjusted (diluted )?eps$",
                   r"eps \(ex-?\s?1x(?!.*\(excl)", r"^diluted eps$",
                   r"^diluted net (income|loss)[\w ()]* per share$", r"^eps$")
_STRIP_FALLBACK_KPI = (r"^margin\s*-\s*ebit \(ex", r"^margin\s*-\s*ebit\b",
                       r"^margin\s*-\s*gross profit", r"gross margin")


def _strip_cell(s, label, rev_s0, rev_val, ctx):
    r = s.row(rev_s0, rev_val, ctx)
    keep = ("ltcode", "name", "display", "unit", "kind", "unit_label", "unit_decimals",
            "unit_status", "currency", "display_value", "latest_value", "latest_growth",
            "prev_growth", "flow_rate_mode", "adjustment_effect")
    out = {k: r.get(k) for k in keep}
    out["label"] = label
    out["prev_period"] = s.periods[-2] if len(s.periods) >= 2 else None
    out["year_ago_period"] = s.periods[-5] if len(s.periods) >= 5 else None
    if s.kind == "pre_growth" and len(s.values) >= 5:
        out["year_ago_rate"] = s.values[-5]
    return out


REIT_STRIP = (("Core FFO/share", r"^core ffo\s*/\s*share$|^core ffo per (diluted )?share"),
              ("Same-store NOI", r"same store noi|same-store noi"),
              ("Occupancy", r"occupan"))


def _reit_strip(usable, rev_s0, rev_val, ctx):
    cells, used = [], set()
    for label, pat in REIT_STRIP:
        cands = [x for x in usable if re.search(pat, x.name or "", re.I) and x.ltcode not in used
                 and (x.unit_info or [None, None, None, None])[3] == "validated"]
        cands.sort(key=lambda x: (0 if "top_kpi" in (x.tags or "") else 1,
                                  0 if x.rankable else 1,
                                  0 if "(basis not given)" not in x.display
                                  and "basis not given" not in x.display else 1,
                                  x.ltcode or ""))
        if cands:
            c = _strip_cell(cands[0], label if label == "Core FFO/share" else cands[0].display,
                            rev_s0, rev_val, ctx)
            cells.append(c)
            used.add(cands[0].ltcode)
    return cells, used


def _quarter_strip(series, rev_s0, rev_val, ctx, routing, guidance, exclusions):
    usable = [x for x in series if x.latest_value is not None and not x.modelled
              and not getattr(x, "segment_cost", False)
              and not getattr(x, "outside_parts", None)
              and not getattr(x, "vendor_ratio_mismatch", None)
              and not getattr(x, "stale", False)]
    cells, used = [], set()
    if (routing or {}).get("template") == "reit":
        cells, used = _reit_strip(usable, rev_s0, rev_val, ctx)
        if len(cells) < 3:
            cells, used = [], set()
    _generic = not cells
    if _generic and rev_s0 is not None and rev_s0.latest_value is not None:
        cells.append(_strip_cell(rev_s0, "Revenue", rev_s0, rev_val, ctx))
        used.add(rev_s0.ltcode)
    eps = None
    for pat in (_STRIP_EPS_PATS if _generic else ()):
        _c = [x for x in usable if re.search(pat, x.name or "", re.I) and x.ltcode not in used
              and not getattr(x, "shadowed_by", None)]
        _c.sort(key=lambda x: (0 if x.obs >= MIN_Q_FOR_RANK else 1, -x.obs,
                               0 if str(x.ltcode).startswith("IS_P_") else 1, x.ltcode or ""))
        eps = _c[0] if _c else None
        if eps is not None:
            break
    if eps is not None:
        c = _strip_cell(eps, "EPS", rev_s0, rev_val, ctx)
        ex = next((e for e in exclusions if e.get("base_ltcode") == eps.ltcode), None)
        if ex:
            c["exclusion"] = {"item": ex.get("item"),
                              "excl_display_value": ex.get("excl_display_value")}
        cells.append(c)
        used.add(eps.ltcode)
    tmpl = (routing or {}).get("template")
    kpi = None
    for pat in (TEMPLATE_PRIORITY.get(tmpl) or [] if _generic else []):
        cands = [x for x in usable if pat.search(x.name or "") and x.ltcode not in used
                 and not re.search(r"^total revenue$|^revenue$|^net revenue$", x.name or "", re.I)
                 and not (x.kind == "level" and (x.unit_info or [None])[0] in (None, "currency")
                          and re.search(r"revenue|sales", x.name or "", re.I))
                 and not SEGMENT_DETAIL_PAT.search(x.name or "")]
        cands.sort(key=lambda x: (0 if x.rankable else 1,
                                  0 if (tmpl == "reit" and PER_SHARE_PAT.search(x.name or "")) else 1,
                                  0 if re.search(r"ex-?\s?1x|adjusted", x.name or "", re.I) else 1,
                                  0 if re.search(r"^(total|consolidated)\b|\btotal\b", x.name or "", re.I)
                                  else (2 if " - " in (x.name or "") else 1),
                                  x.ltcode))
        if cands:
            kpi = cands[0]
            break
    if kpi is None and _generic:
        for pat in _STRIP_FALLBACK_KPI:
            kpi = next((x for x in usable if re.search(pat, x.name or "", re.I)
                        and x.ltcode not in used), None)
            if kpi is not None:
                break
    if kpi is not None:
        cells.append(_strip_cell(kpi, "Key metric", rev_s0, rev_val, ctx))
        used.add(kpi.ltcode)
    guide = None
    revs = list((guidance or {}).get("revisions") or [])
    _rc = rev_s0.ltcode if rev_s0 is not None else None
    def _grank(r):
        nm = str(r.get("name") or "")
        top = (r.get("ltcode") == _rc or re.fullmatch(r"(total |net )?revenues?|net sales", nm.strip(), re.I))
        per_share = re.search(r"\beps\b|ffo.*/\s*share|ffo per share", nm, re.I)
        adj = re.search(r"ex-?\s?1x|adjusted|core", nm, re.I)
        return (0 if (top and not r.get("is_percentage")) else
                1 if (per_share and adj) else 2 if per_share else 3,
                str(r.get("target_period")), str(r.get("ltcode")))
    revs.sort(key=_grank)
    if revs and _grank(revs[0])[0] < 3:
        g = revs[0]
        guide = {k: g.get(k) for k in ("ltcode", "display", "name", "target_period", "direction",
                                         "first_display", "latest_display", "first_text", "latest_text",
                                         "changed_in", "first_source_period", "latest_source_period",
                                         "items_only")}
        guide["label"] = "FY guide"
    return {"cells": cells, "guide": guide}


ONE_TIME_GROUPS = (
    ("litigation", r"litigation"),
    ("severance", r"severance"),
    ("restructuring", r"restructuring"),
    ("impairment", r"impairment"),
    ("acquisition costs", r"acquisition[- ]related"),
    ("acquired IPR&D", r"^acquired in-?process|^acquired ipr&d|^ipr&d"),
)


def _year_ago_label(q):
    m = re.match(r"^([1-4])Q(\d{2})$", str(q or ""))
    return ("%sQ%02d" % (m.group(1), int(m.group(2)) - 1)) if m else None


def _prev_quarter_label(q):
    m = re.match(r"^([1-4])Q(\d{2})$", str(q or ""))
    if not m:
        return None
    qn, yy = int(m.group(1)), int(m.group(2))
    return ("%dQ%02d" % (qn - 1, yy)) if qn > 1 else ("4Q%02d" % (yy - 1))


def _ebitda_bridge(e, series_by_code):
    q = e.periods[-1] if e.periods else None
    if q is None or e.latest_value is None:
        return None
    def _at(pat, excl=None):
        for x in series_by_code.values():
            if re.fullmatch(pat, (x.name or "").strip(), re.I) and x.periods and x.periods[-1] == q \
                    and x.latest_value is not None and (excl is None or not re.search(excl, x.name, re.I)):
                return x
        return None
    adj = bool(re.search(r"ex-?\s?1x|adjusted", e.name or "", re.I))
    ebit = (_at(r"ebit\s*-\s*adjusted|ebit\s*\(ex-?\s?1x items?\)") if adj else None) or _at(r"ebit")
    dep = _at(r"depreciation")
    amo = _at(r"amortization")
    if ebit is None or dep is None or amo is None:
        return None
    tot = ebit.latest_value + abs(dep.latest_value) + abs(amo.latest_value)
    if abs(tot - e.latest_value) > 0.005 * abs(e.latest_value):
        return None
    cur = getattr(e, "currency", None) or _DEFAULT_CUR.get("sym")
    return ("%s %s = %s %s + depreciation %s + amortization %s"
            % (e.display, _money(e.latest_value, cur), ebit.display, _money(ebit.latest_value, cur),
               _money(abs(dep.latest_value), cur), _money(abs(amo.latest_value), cur)))


_NIO_NAME = re.compile(r"(net\s+)?interest\s*&\s*other\s+income\s*/\s*expenses?", re.I)

_BELOW_EBIT_CODES = re.compile(r"^IS_P_(117|118|123|124|127|128|251|252)(-M)?$")
_BELOW_EBIT_NAME = re.compile(r"net income|net earnings|\beps\b|earnings per share|pre-?tax|\bpbt\b|"
                              r"net margin", re.I)


def _is_non_operating_line(x):
    return (x.ltcode == "IS_P_157" or bool(_NIO_NAME.fullmatch((x.name or "").strip()))) \
        and not re.search(r"\(ex-?\s?1x", x.name or "", re.I)


def _non_operating_word(series, nio, q):
    vals = dict(zip(nio.periods, nio.values))
    pbt = next((x for x in series if x.ltcode == "IS_P_117"), None)
    ebit = next((x for x in series if x.ltcode == "IS_P_109"), None)
    if pbt is not None and ebit is not None and vals.get(q) is not None:
        pv, ev = dict(zip(pbt.periods, pbt.values)), dict(zip(ebit.periods, ebit.values))
        gap = {k: pv[k] - ev[k] for k in pv if k in ev and pv[k] is not None and ev[k] is not None}
        others_i = [v for k, v in vals.items() if k != q and v is not None]
        others_g = [v for k, v in gap.items() if k != q]
        if q in gap and len(others_i) >= 3 and len(others_g) >= 3:
            di = vals[q] - _median(others_i)
            dg = gap[q] - _median(others_g)
            if di and dg and 0.75 <= abs(dg) / abs(di) <= 1.33:
                same_sign = (di > 0) == (dg > 0)
                v = vals[q]
                is_income = (v > 0) if same_sign else (v < 0)
                return "net interest and other " + ("income" if is_income else "expense")
    conv = _income_sign_convention(series)
    if conv is not None and vals.get(q) is not None:
        is_income = (vals[q] < 0) if conv == "income_negative" else (vals[q] > 0)
        return "net interest and other " + ("income" if is_income else "expense")
    return "net interest and other income or expense"


def _median(xs):
    xs = sorted(xs)
    n = len(xs)
    return (xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2.0) if n else 0.0


def _non_operating_window(series, payload):
    q = payload.get("latest_quarter")
    nio = next((x for x in series if _is_non_operating_line(x)), None)
    ebit = next((x for x in series if x.ltcode in ("IS_P_109", "IS_P_110")), None)
    if nio is None or ebit is None or not q:
        return []
    vals = {k: v for k, v in zip(nio.periods, nio.values) if v is not None}
    ev = {k: v for k, v in zip(ebit.periods, ebit.values) if v is not None}
    prev = _prev_quarter_label(q)
    roles = [(prev, "last quarter"), (_year_ago_label(q), "the year-ago quarter"),
             (_year_ago_label(prev) if prev else None, "last quarter's year-ago quarter")]
    cur = getattr(nio, "currency", None) or _DEFAULT_CUR.get("sym")
    out = []
    for per, role in roles:
        if not per or per not in vals or not ev.get(per):
            continue
        others = [v for k, v in vals.items() if k != per]
        if len(others) < 4:
            continue
        dev = vals[per] - _median(others)
        if abs(dev) < 0.10 * abs(ev[per]):
            continue
        out.append({"period": per, "role": role,
                    "display": _non_operating_word(series, nio, per),
                    "value_display": _money(abs(vals[per]), cur),
                    "share_of_operating_profit_pct": round(abs(vals[per]) / abs(ev[per]) * 100.0, 1),
                    "ltcode": nio.ltcode})
    if not out:
        return []
    for key in ("accelerating", "decelerating", "deep_dive", "headline_moves", "primary_context",
                "quiet_headline"):
        for r in payload.get(key) or []:
            if _below_ebit_row(r):
                r["non_operating_window"] = out
    for c in ((payload.get("quarter_strip") or {}).get("cells") or []):
        if _below_ebit_row(c):
            c["non_operating_window"] = out
    return out


def _below_ebit_row(r):
    code = str(r.get("ltcode") or "")
    name = str(r.get("name") or "") + " " + str(r.get("display") or "")
    if re.search(r"ex-?\s?1x", name, re.I):
        return False
    return bool(_BELOW_EBIT_CODES.match(code) or (code.startswith("IS_") and _BELOW_EBIT_NAME.search(name)))


def _one_time_items(series, latest_q):
    out = []
    _used = set()
    for group, pat in ONE_TIME_GROUPS:
        cands = [x for x in series
                 if x.ltcode not in _used and re.search(pat, x.name or "", re.I)
                 and not (x.name or "").strip().startswith("%")
                 and not re.search(r"\(ex-?\s?1x|\(excl|\bexcluding\b|\bex-|\(ex[.\s]|eps|per share|"
                                   r"margin|total (non-?interest )?expense", x.name or "", re.I)
                 and x.periods and x.periods[-1] == latest_q
                 and x.latest_value not in (None, 0)]
        if not cands:
            continue
        x = max(cands, key=lambda y: (abs(y.latest_value or 0), y.ltcode))
        _used.add(x.ltcode)
        ya = _year_ago_label(latest_q)
        prior = None
        if ya in x.periods:
            prior = x.values[x.periods.index(ya)]
        cur = getattr(x, "currency", None) or _DEFAULT_CUR.get("sym")
        _rev = next((y for y in series if y.ltcode == "IS_P_101"), None)
        _rv = dict(zip(_rev.periods, _rev.values)) if _rev else {}
        _bps = (round(abs(x.latest_value) / abs(_rv[latest_q]) * 1e4)
                if _rv.get(latest_q) else None)
        _bps_ya = (round(abs(prior) / abs(_rv[ya]) * 1e4)
                   if prior not in (None, 0) and _rv.get(ya) else None)
        if _bps is not None and _bps < 5:
            continue
        out.append({
            "revenue_bps": _bps, "year_ago_revenue_bps": _bps_ya,
            "group": group, "ltcode": x.ltcode, "name": x.name, "display": x.display,
            "latest_value": x.latest_value, "latest_display": _money(abs(x.latest_value), cur),
            "year_ago_period": ya,
            "year_ago_value": prior,
            "year_ago_display": (_money(abs(prior), cur) if prior not in (None, 0) else None),
        })
    return out[:5]


def _heatmap(rows, series_by_code):
    hm = []
    for r in rows:
        s = series_by_code[r["ltcode"]]
        cells = []
        accel = s.accel[-HEATMAP_QUARTERS:]
        periods = s.periods[-HEATMAP_QUARTERS:]
        hist_all = [a for a in s.accel if a is not None]
        mu = statistics.fmean(hist_all) if len(hist_all) >= 2 else 0.0
        sd = statistics.pstdev(hist_all) if len(hist_all) >= 2 else 0.0
        growth = s.growth[-HEATMAP_QUARTERS:]
        for p, a, g in zip(periods, accel, growth):
            z = None if (a is None or sd <= 0) else (a - mu) / sd
            cells.append({"period": p, "accel": a, "growth": g,
                          "z": _clamp_display(z), "bin": _bin_z(z)})
        hm.append({"ltcode": r["ltcode"], "name": r["name"],
                   "display": r.get("display") or s.display,
                   "kind": s.kind,
                   "unit": r["unit"], "cells": cells})
    return hm


def _bps_dp(unit, dp):
    return 0 if unit != "pct" else dp


def _money(v, cur=None):
    if v is None:
        return "level withheld"
    p_ = cur or ""
    a = abs(v)
    sg = "-" if v < 0 else ""
    for cut, suf in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")):
        if a >= cut:
            return f"{sg}{p_}{a / cut:,.2f}{suf}"
    return f"{sg}{p_}{a:,.2f}"


def format_row_level(v, unit_label, decimals, withheld, cur=None):
    if v is None or withheld:
        return "level withheld"
    if unit_label in (None, "currency") or (unit_label == "count" and cur):
        return _money(v, cur)
    if unit_label == "count":
        a = abs(v)
        for cut, suf in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")):
            if a >= cut:
                return f"{v / cut:,.2f}{suf}"
        return f"{v:,.0f}"
    dp = 2 if decimals is None else decimals
    if unit_label not in ("%", "x") and abs(v) >= 1e6:
        a = abs(v)
        for cut, suf in ((1e12, "T"), (1e9, "B"), (1e6, "M")):
            if a >= cut:
                return f"{v / cut:,.2f}{suf} {unit_label}"
    return f"{v:,.{dp}f}" + ("%" if unit_label == "%" else f" {unit_label}")


def fmt_number(v, dp=1, signed=False):
    if v is None:
        return "n/a"
    r = round(float(v), dp)
    if r == 0:
        return f"{0.0:,.{dp}f}"
    return f"{r:+,.{dp}f}" if signed else f"{r:,.{dp}f}"


def _fmt_growth(v, unit, dp=1):
    if v is None:
        return "n/a"
    return fmt_number(v, _bps_dp(unit, dp), signed=True) + ('%' if unit == 'pct' else ' bps')


def _fmt_accel(v, unit, dp=1):
    if v is None:
        return "n/a"
    return fmt_number(v, _bps_dp(unit, dp), signed=True) + (' pp' if unit == 'pct' else ' bps')


DISPLAY_SECTIONS = (
    ("headline_moves", "rows"), ("accelerating", "rows"), ("decelerating", "rows"),
    ("deep_dive", "rows"), ("quiet_headline", "rows"), ("heatmap", "rows"),
    ("monitor", "rows"), ("cash_conversion", "cash"), ("divergences", "divergences"),
    ("guidance_check", "guidance"),
)


def _displayed_ltcodes(payload):
    found, labels = {}, {}

    def take(code, where):
        if not code:
            return
        found.setdefault(code, set()).add(where)

    for key, shape in DISPLAY_SECTIONS:
        blob = payload.get(key)
        if not blob:
            continue
        if shape == "rows":
            for r in blob:
                take(r.get("ltcode"), key)
        elif shape == "cash":
            for r in (blob.get("rows") or []):
                take(r.get("ltcode"), key)
        elif shape == "divergences":
            for d in blob:
                for m in (d.get("metrics") or []):
                    take(m.get("ltcode"), key)
        elif shape == "guidance":
            for bucket in ("scored", "other_horizon", "forward_uncompared",
                           "delivered", "delivered_uncompared"):
                for g in (blob.get(bucket) or []):
                    take(g.get("ltcode"), key)
                    take(g.get("resolved_ltcode"), key)
                    if g.get("ltcode") and g.get("name"):
                        labels.setdefault(g["ltcode"], g["name"])
            for n in (blob.get("outlook_notes") or []):
                take(n.get("matched_ltcode"), key)
    return found, labels


def _provenance(payload, series_by_code):
    where, labels = _displayed_ltcodes(payload)
    rows = []
    for code in sorted(where):
        s = series_by_code.get(code)
        if s is None:
            continue
        rows.append({
            "ltcode": s.ltcode, "uic": s.uic, "name": s.name, "display": s.display,
            "quarters": s.obs,
            "first_period": (s.periods or [None])[0],
            "last_period": (s.periods or [None])[-1],
            "source_mix": dict(sorted(s.sources.items())),
            "class": s.provenance or "document class not returned",
            "unit": s.unit,
            "definition_stable": "step_series" not in (s.flags or []),
            "confidence": s.confidence,
            "tier": s.tier,
            "shown_in": sorted(where[code]),
        })
    return rows


def _not_retrieved(raw, series):
    have = {s.ltcode for s in series}
    fetched = {str(r.get("LTCODE") or r.get("ltcode") or "").strip()
               for r in (raw.get("actuals") or [])} - {""}
    planned = raw.get("planned_codes") or []
    detail = raw.get("codes_absent_detail") or {}
    _plain = {"annual_only": "reported annually only",
              "no_data": "no data in the feed, quarterly or annual",
              "annual_not_probed": "returned nothing quarterly; annual figures not checked"}
    rows = [{"ltcode": c.get("ltcode"), "name": c.get("name"),
             "status": _plain.get(detail.get(c.get("ltcode")), "not fetched in this run")}
            for c in planned if c.get("ltcode") not in have and c.get("ltcode") not in fetched]
    return {
        "planned": len(planned) or None,
        "retrieved": len(have),
        "missing_count": len(rows),
        "missing": sorted(rows, key=lambda r: r["ltcode"] or ""),
        "fetched_not_scored": len({c.get("ltcode") for c in planned} & (fetched - have)),
        "note": ("Discovered by the scan plan and not read in this run; each carries "
                 "its status (not fetched, reported annually only, or no data in the "
                 "feed). No statement in this note covers them. Lines that were read "
                 "and then held out of scoring are in the excluded list, with reasons."),
    } if planned else {"planned": None, "retrieved": len(have), "missing_count": 0,
                       "missing": [], "note": ""}


def _source_records(raw, payload):
    docs, guides = [], []
    seen_doc = set()

    gc = payload.get("guidance_check") or {}
    for bucket in ("delivered", "scored", "forward_uncompared",
                   "delivered_uncompared", "other_horizon", "outlook_notes"):
        for g in (gc.get(bucket) or []):
            mid = g.get("guide_midpoint")
            if mid is not None:
                status = "parsed"
            elif g.get("reason"):
                status = UNCOMPARED_STATUS.get(g["reason"], g["reason"])
            elif bucket in ("other_horizon", "outlook_notes"):
                status = "no comparison attempted on this horizon"
            else:
                status = "no figure could be read from the guide text"
            guides.append({
                "bucket": bucket,
                "ltcode": g.get("ltcode") or g.get("matched_ltcode"),
                "metric": g.get("display") or g.get("name") or g.get("label"),
                "source_name": g.get("name"),
                "target_period": g.get("target_period"),
                "source_period": g.get("source_period"),
                "raw_guide": g.get("guide_text") or g.get("text"),
                "parsed_midpoint": mid,
                "parsed_unit": g.get("unit"),
                "parse_status": status,
                "guide_implied_growth": g.get("guide_implied_growth"),
                "continuation_threshold": g.get("continuation_threshold"),
                "reported_actual": g.get("actual"),
                "versus_guide": g.get("versus_display"),
                "verdict": g.get("verdict"),
                "document": g.get("guide_source"),
            })
            key = (g.get("guide_source") or "", g.get("source_period") or "")
            if g.get("guide_source") and key not in seen_doc:
                seen_doc.add(key)
                docs.append({"document": g.get("guide_source"),
                             "class": "guidance",
                             "given_with": g.get("source_period"),
                             "used_for": "guidance continuation test"})

    return {
        "documents": docs,
        "guidance_rows": guides,
        "guidance_rows_returned": len(_guidance_arrays(raw) or []) if raw else None,
        "note": ("Quoted commentary carries its document_id and page inline beside each "
                 "quotation. Guidance rows list the raw guide string alongside the parsed "
                 "midpoint so a parse can be checked against the source text."),
    }


def _ranking_disclosure(payload):
    k = payload.get("constants") or {}
    weights = {
        "unusualness": W_UNUSUAL, "tier_weight": W_TIER,
        "materiality": W_MATERIAL, "confidence_weight": W_CONFIDENCE,
    }
    rows = {}
    seen = set()
    for key in ("headline_moves", "accelerating", "decelerating", "deep_dive"):
        for r in (payload.get(key) or []):
            code = r.get("ltcode")
            if not code or code in seen:
                continue
            seen.add(code)
            comp = r.get("priority_components") or {}
            terms = {}
            total = 0.0
            for cname, w in weights.items():
                val = comp.get(cname)
                contrib = None if val is None else round(w * float(val), 6)
                if contrib is not None:
                    total += contrib
                terms[cname] = {"weight": w, "normalised": val, "weighted": contrib}
            adj = comp.get("template_adjustment")
            if adj is not None:
                total += float(adj)
                terms["template_adjustment"] = {
                    "weight": 1.0, "normalised": adj, "weighted": adj,
                    "template_rank": comp.get("template_rank"),
                    "basis": ("uplift for a metric the sector template leads with, less a "
                              "penalty where the line is a regional or segment cut of a "
                              "total the company also reports")}
            rows[code] = {
                "display": r.get("display") or r.get("name"),
                "terms": terms,
                "recomputed_total": round(total, 4),
                "stored_priority": r.get("priority"),
                "matches": (r.get("priority") is not None
                            and abs(round(total, 4) - r["priority"]) <= 0.0011),
                "rounding_tolerance": 0.0011,
            }
    return {
        "formula": ("priority = 0.40*unusualness + 0.30*tier_weight + "
                    "0.20*materiality + 0.10*confidence_weight"),
        "weights": weights,
        "template_adjustment_bounds": {"max_uplift": TEMPLATE_BONUS,
                                       "segment_detail_penalty": SEGMENT_DETAIL_PENALTY},
        "tier_scores": {str(kk): vv for kk, vv in TIER_SCORE.items()},
        "confidence_scores": dict(CONF_SCORE),
        "component_definitions": {
            "unusualness": ("min(1, |z| / 3.0). z scores the latest acceleration against "
                            "this same line's own acceleration history, which is what makes "
                            "a subscriber count and a gross margin comparable on one list."),
            "tier_weight": ("TIER_SCORE lookup on the row's business tier "
                            "(1 Primary, 2 Supporting, 3 Contextual)."),
            "materiality": ("For a level with revenue available: min(1, |value| / (0.25 * "
                            "revenue)), floored at 0.5 where the share is under 1e-4 because "
                            "that means incomparable units rather than a trivial line. For a "
                            "ratio or a pre-derived growth line: 0.75, since a margin has no "
                            "size but is never trivial. Otherwise 0.5."),
            "confidence_weight": ("CONF_SCORE lookup on the confidence grade, which is "
                                  "graded from history length, gaps, source class, definition "
                                  "stability and base sensitivity."),
        },
        "materiality_gates_note": (
            "The composite orders rows. It does not decide which rows qualify. A row must "
            "separately clear both materiality gates to appear at all: |z| at or above "
            f"{k.get('material_z')} AND an absolute acceleration at or above "
            f"{k.get('min_abs_accel_pp')}pp for a level or "
            f"{k.get('min_abs_accel_bps')}bps for a ratio."),
        "rows": rows,
        "all_rows_reconcile": all(v["matches"] for v in rows.values()) if rows else True,
    }


def _ph_growth(r, key):
    v = r.get(key)
    if r.get("kind") == "pre_growth" and v is not None:
        _vs = [x for x in (r.get("value_series") or []) if x is not None]
        _dp = 0 if _vs and all(float(x).is_integer() for x in _vs) else 1
        return ("%." + str(_dp) + "f%%") % v
    return _fmt_growth(v, r["unit"])


def _pair_question(legs):
    a, b = legs[0], legs[1]
    ga, gb = a.get("growth"), b.get("growth")
    if ga is not None and gb is not None and a.get("kind") != "ratio" and b.get("kind") != "ratio":
        hi, lo = (a, b) if ga >= gb else (b, a)
        return "Does %s keep growing faster than %s?" % (hi.get("display"), lo.get("display"))
    rat = next((m for m in (a, b) if m.get("kind") == "ratio"), None)
    lev = next((m for m in (a, b) if m.get("kind") != "ratio"), None)
    if rat is not None and lev is not None and rat.get("growth") is not None:
        wd = "widening" if rat["growth"] > 0 else "narrowing"
        ac = lev.get("accel")
        lw = ("while %s growth slows" if (ac is not None and ac < 0) else
              "while %s growth picks up") % lev.get("display")
        return "Does %s keep %s %s?" % (rat.get("display"), wd, lw)
    return "Does the gap between them close, or widen?"


def _pair_proof(legs, series_by_code):
    bits, base = [], None
    for m in legs:
        s = series_by_code.get(m.get("ltcode"))
        fb = (s.forward_bar() if s is not None else None) or {}
        if s is None or fb.get("level_to_hold_display") is None:
            return None
        lab, div, dp, status, _ = s.unit_info
        lvl = format_row_level(fb["level_to_hold_display"], lab, dp,
                               status == "conflicted", getattr(s, "currency", None))
        if lvl == "level withheld":
            return None
        bits.append("%s %s" % (s.display, lvl))
        base = base or fb.get("next_base_period")
    return " and ".join(bits) + " hold their y/y rates against " + str(base or "the year-ago quarter")


def _placeholders(payload):
    ph = {
        "ticker": payload["ticker"],
        "company": payload.get("company") or payload["ticker"],
        "latest_q": payload["latest_quarter"],
        "quarters_scanned": str(payload["coverage"]["quarters"]),
        "direction": payload.get("direction", "Mixed"),
        "series_scanned": str(payload["coverage"]["series_scanned"]),
        "series_ranked": str(payload["coverage"]["series_ranked"]),
    }
    for i, r in enumerate(payload["accelerating"][:DEEP_DIVE_N], 1):
        ph[f"acc{i}.name"] = r.get("display") or r["name"]
        ph[f"acc{i}.z"] = f"{abs(r['z_display']):.1f}"
        ph[f"acc{i}.accel"] = _fmt_accel(r["latest_accel"], r["unit"])
        ph[f"acc{i}.growth"] = _ph_growth(r, "latest_growth")
        ph[f"acc{i}.streak"] = str(r["streak"])
    for i, r in enumerate(payload["decelerating"][:DEEP_DIVE_N], 1):
        ph[f"dec{i}.name"] = r.get("display") or r["name"]
        ph[f"dec{i}.z"] = f"{abs(r['z_display']):.1f}"
        ph[f"dec{i}.accel"] = _fmt_accel(r["latest_accel"], r["unit"])
        ph[f"dec{i}.growth"] = _ph_growth(r, "latest_growth")
        ph[f"dec{i}.streak"] = str(r["streak"])
    for i, r in enumerate(payload["deep_dive"], 1):
        ph[f"dd{i}.name"] = r.get("display") or r["name"]
        _ae = r.get("adjustment_effect") or {}
        if _ae.get("size_display"):
            ph[f"dd{i}.effect"] = (("a charge of " if _ae.get("charge") else "")
                                   + _ae["size_display"] + (" for " if _ae.get("charge") else " from ")
                                   + _ae["label"] + " (" + _ae["kind"] + ", "
                                   + ("carried from the gross margin" if _ae.get("carried_from")
                                      else "per management") + ")")
        ph[f"dd{i}.implication"] = r.get("implication") or "Ambiguous"
        ph[f"dd{i}.confidence"] = r.get("confidence") or "Withheld"
        ph[f"dd{i}.dir"] = r["streak_dir"]
        ph[f"dd{i}.z"] = f"{abs(r['z_display']):.1f}"
        ph[f"dd{i}.accel"] = _fmt_accel(r["latest_accel"], r["unit"])
        ph[f"dd{i}.growth"] = _ph_growth(r, "latest_growth")
        ph[f"dd{i}.prev_growth"] = _ph_growth(r, "prev_growth")
        _ps = r.get("period_series") or []
        ph[f"dd{i}.prev_q"] = _ps[-2] if len(_ps) >= 2 else "last quarter"
        ph[f"dd{i}.streak"] = str(r["streak"])
        ph[f"dd{i}.quarters"] = str(r["quarters"])
        ph[f"dd{i}.comp"] = r["comp_flag"] or "none"
        _lab = r.get("unit_label")
        _withheld = (r.get("unit_status") == "conflicted" or r.get("flow_rate_mode"))
        _dp = r.get("unit_decimals", 2)
        if _dp is None:
            _dp = 2
        if r.get("kind") == "pre_growth":
            _vs = [x for x in (r.get("value_series") or []) if x is not None]
            _dp = 0 if _vs and all(float(x).is_integer() for x in _vs) else 1

        _cur = r.get("currency")

        def _lvl(v):
            return format_row_level(v, _lab, _dp, _withheld, _cur)
        ph[f"dd{i}.level"] = _lvl(r.get("display_value") if not _withheld else None)
        ph[f"dd{i}.prior_level"] = _lvl(r.get("prior_year_value"))
        ph[f"dd{i}.unit"] = _lab or "reported units"
        fb = r.get("forward_bar") or {}
        ph[f"dd{i}.threshold"] = _lvl(fb.get("level_to_hold_display"))
        ph[f"dd{i}.base_q"] = fb.get("next_base_period") or "the year-ago quarter"
        _yc = r.get("year_ago_comparison") or {}
        if _yc.get("two_year_change_display"):
            ph[f"dd{i}.ya_growth"] = _yc["year_ago_growth_display"]
            ph[f"dd{i}.two_year"] = _yc["two_year_change_display"]
            ph[f"dd{i}.two_year_q"] = _yc["two_year_period"]
    _gc = payload.get("guidance_check") or {}
    ph["guide.next_q"] = _gc.get("next_quarter") or "the next quarter"
    for i, r in enumerate(_gc.get("scored") or [], 1):
        ph[f"fg{i}.name"] = r.get("display") or r.get("name") or ""
        _v = _guide_placeholder(r.get("guide_display_text"), r.get("guide_text"),
                                r.get("guide_scale"))
        if _v is not None:
            ph[f"fg{i}.guide"] = _v
        ph[f"fg{i}.implied"] = _fmt_growth(r.get("guide_implied_growth"), r.get("unit", "pct")) \
            if r.get("guide_implied_growth") is not None else "not implied"
        ph[f"fg{i}.latest"] = _fmt_growth(r.get("latest_growth"), r.get("unit", "pct")) \
            if r.get("latest_growth") is not None else "n/a"
        ph[f"fg{i}.verdict"] = r.get("verdict_display") or r.get("verdict") or ""
    for i, r in enumerate(_gc.get("delivered") or [], 1):
        ph[f"dg{i}.name"] = r.get("display") or r.get("name") or ""
        ph[f"dg{i}.guide"] = r.get("guide_display_text") or ""
        ph[f"dg{i}.actual"] = r.get("actual_display") or ""
        ph[f"dg{i}.versus"] = r.get("versus_display") or ""
    for i, r in enumerate(_gc.get("revisions") or [], 1):
        ph[f"fy{i}.name"] = r.get("display") or r.get("name") or ""
        for _side in ("first", "latest"):
            _v = _guide_placeholder(r.get(_side + "_display"), r.get(_side + "_text"),
                                    r.get(_side + "_scale"))
            if _v is not None:
                ph[f"fy{i}.{_side}"] = _v
        ph[f"fy{i}.first_q"] = r.get("first_source_period") or ""
        ph[f"fy{i}.direction"] = r.get("direction") or ""
        if r.get("items_only"):
            ph[f"fy{i}.direction"] = "lowered to include one-time items"
            ph[f"fy{i}.adjusted"] = str(r["items_only"].get("adjusted_guide") or "")
            ph[f"fy{i}.adjusted_name"] = str(r["items_only"].get("adjusted_display") or "")
        ph[f"fy{i}.period"] = str(r.get("target_period") or "")
    for i, r in enumerate(_gc.get("other_horizon") or [], 1):
        ph[f"fh{i}.name"] = r.get("display") or r.get("name") or ""
        _v = _guide_placeholder(r.get("guide_display_text"), r.get("guide_text"),
                                r.get("guide_scale"))
        if _v is not None:
            ph[f"fh{i}.guide"] = _v
        ph[f"fh{i}.period"] = str(r.get("target_period") or "")
    _nx = [n for n in (_gc.get("outlook_notes") or []) if n.get("note_value_display")]
    for i, n in enumerate(_nx, 1):
        ph[f"gn{i}.name"] = n.get("matched_display") or ""
        ph[f"gn{i}.value"] = n.get("note_value_display") or ""
        ph[f"gn{i}.level"] = n.get("matched_level_display") or ""
        ph[f"gn{i}.period"] = str(n.get("target_period") or "")
    _bases = {(r.get("forward_bar") or {}).get("next_base_period")
              for r in payload["deep_dive"]} - {None}
    ph["base_q"] = (next(iter(_bases)) if len(_bases) == 1 else "the year-ago quarter")
    for i, d in enumerate(payload["divergences"], 1):
        ph[f"div{i}.headline"] = d["headline"]
        for j, m in enumerate(d["metrics"], 1):
            ph[f"div{i}.m{j}.name"] = m.get("display") or m["name"]
            ph[f"div{i}.m{j}.z"] = f"{abs(m['z']):.1f}" if m["z"] is not None else "n/a"
    return ph


BASIS_SUFFIX_PAT = re.compile(
    r"\s*-\s*(?:[A-Z][\w&.]*\s+){0,3}share$|\s*-\s*owned\s*(?:&|and)\s*managed$", re.I)


def _detect_splices(series):
    by_tail = {}
    for x in series:
        v = [a for a in (x.values or []) if a is not None]
        if x.kind != "level" or len(v) < 8 or not v[-1] or not v[-2]:
            continue
        if min(abs(v[-1]), abs(v[-2])) < 5e7:
            continue
        by_tail.setdefault((round(v[-2], 6), round(v[-1], 6)), []).append(x)
    for grp in by_tail.values():
        if len(grp) < 2:
            continue
        for a in grp:
            for b in grp:
                if a is b:
                    continue
                pa = dict(zip(a.periods, a.values))
                pb = dict(zip(b.periods, b.values))
                shared = [q for q in a.periods[:-2] if pa.get(q) is not None and pb.get(q) is not None]
                differ = sum(1 for q in shared if abs(pa[q] - pb[q]) > 1e-9 * max(abs(pa[q]), 1))
                _rev = r"revenue|net sales|^sales$"
                if re.search(_rev, a.name or "", re.I) and re.search(_rev, b.name or "", re.I) \
                        and len(shared) >= 4 and differ >= 4 and (all(pb[q] <= pa[q] + 1e-6 * abs(pa[q]) for q in shared)
                                        or all(pa[q] <= pb[q] + 1e-6 * abs(pb[q]) for q in shared)):
                    _part = b if all(pb[q] <= pa[q] + 1e-6 * abs(pa[q]) for q in shared) else a
                    _whole = a if _part is b else b
                    if not getattr(_part, "shadowed_by", None):
                        _part.shadowed_by = _whole.name
                    continue
                if len(shared) >= 4 and differ >= 4:
                    if not getattr(a, "spliced_with", None):
                        a.spliced_with = b.name
                        a.flags.append("spliced_series")


INSYNC_CONSTRUCT_PAT = re.compile(
    r"ex-?\s?1x|\badj(usted)?\b|\(adj\)|ebitda|average membership|^\(.*\)\s*/|"
    r"tax credits?$", re.I)


def _is_calculated(x):
    src = {str(k).strip().lower(): v for k, v in (x.sources or {}).items()}
    tot = sum(src.values()) or 0
    return bool(tot) and src.get("calculated", 0) * 2 >= tot


def _label_insync_calculated(series):
    by_name = {(x.name or "").strip().lower(): x for x in series}
    for x in series:
        if not _is_calculated(x) or not INSYNC_CONSTRUCT_PAT.search(x.name or ""):
            continue
        if x.kind == "ratio":
            _m = (re.match(r"^(?:margin|gm|%\s*revenue)\s*-\s*(.+)$", x.name or "", re.I)
                  or re.match(r"^%\s*[^-]+-\s*(.+)$", x.name or "", re.I))
            _num = by_name.get(_m.group(1).strip().lower()) if _m else None
            if _num is not None and not _is_calculated(_num):
                continue
        d = getattr(x, "_display_override", None) or x.display
        d = re.sub(r"^(EPS|EBIT|EBITDA|Net Income)\s*\(Ex-?\s?1x Items?\)$", r"Adjusted \1", d, flags=re.I)
        if not d.startswith("InSync-Calculated"):
            x._display_override = "InSync-Calculated " + d
            x.insync_calculated = True


def _check_revenue_shares(series):
    by = {(x.name or "").strip().lower(): x for x in series}
    tot = by.get("total revenue") or by.get("revenue")
    if tot is None:
        return
    for x in series:
        m = re.match(r"^%\s*revenue\s*-\s*(.+)$", (x.name or "").strip(), re.I)
        if not m or x.kind != "ratio" or getattr(x, "identity_failed", None):
            continue
        num = by.get(m.group(1).strip().lower())
        if num is None or num.kind != "level":
            continue
        a, n_, t_ = x.as_points()[-4:], num.as_points()[-4:], tot.as_points()[-4:]
        bad, seen = 0, 0
        for v, n, t in zip(a, n_, t_):
            if None in (v, n, t) or not t:
                continue
            seen += 1
            if abs(v - 100.0 * n / t) > 1.0:
                bad += 1
        if seen >= 3 and bad >= seen - 1:
            x.identity_failed = ("the source's share does not equal %s over total revenue in "
                                 "recent quarters, so it measures something else" % (num.display or num.name))


COST_OF_REVENUE_PAT = re.compile(
    r"^cogs\b|cost of (goods|revenues?|sales|products|services)|^cost of|^sg&a\b|selling,? general|"
    r"^opex\b|^operating expenses|^total operating expenses|^cost & expenses", re.I)


BARE_PLACE_METRIC_PAT = re.compile(
    r"revenue|sales|income|profit|margin|cost|expense|price|volume|growth|rate|ratio|share|"
    r"count|units?|stores?|orders?|backlog|cash|debt|assets?|capex|eps|ebit|days|yield|"
    r"mix|contribution|impact|effect|total|other|%", re.I)


def _name_bare_growth_rates(series):
    for x in series:
        nm = (x.name or "").strip()
        if not x._bare_place_rate():
            continue
        BARE_PLACE_NAMES.add((x.name or "").strip())
        x._display_override = nm + " Growth"
        x._polarity_override = 1


def _eps_basis(series):
    dil = [x for x in series if x.kind == "level" and x.latest_value is not None
           and re.match(r"^diluted (eps|net (income|loss)[\w ()]* per share)$", (x.name or "").strip(), re.I)]
    for x in series:
        if x.kind == "level" and re.match(r"^(diluted|basic) (eps|net (income|loss)[\w ()]* per share)$",
                                          (x.name or "").strip(), re.I):
            x._polarity_override = 1
            x._company_published = True
    for x in series:
        if x.provenance != "derived" or x.kind != "level" or getattr(x, "shadowed_by", None):
            continue
        if not re.match(r"^eps \(ex-?\s?1x items?\)$", (x.name or "").strip(), re.I):
            continue
        pa = dict(zip(x.periods, x.as_points()))
        for d in dil:
            pb = dict(zip(d.periods, d.as_points()))
            last = [q for q in x.periods[-4:] if pa.get(q) is not None and pb.get(q) is not None]
            if len(last) == 4 and x.periods[-1] in last and all(
                    abs(pa[q] - pb[q]) <= 0.005 * max(abs(pb[q]), 0.01) for q in last):
                x.shadowed_by = d.display
                x.shadow_of_code = d.ltcode
                x.shadow_kind = "no_adjustments"
                d.no_adjustments = True
                break
    _cadj = [x for x in series if x.kind == "level" and x.provenance == "filed"
             and re.fullmatch(r"adjusted (diluted )?eps", (x.name or "").strip(), re.I)
             and x.latest_value is not None]
    if _cadj:
        for x in series:
            if x.provenance == "derived" and x.kind == "level" and not getattr(x, "shadowed_by", None) \
                    and re.match(r"^eps \(ex-?\s?1x items?\)$", (x.name or "").strip(), re.I):
                x.shadowed_by, x.shadow_of_code, x.shadow_kind = _cadj[0].display, _cadj[0].ltcode, "company_adjusted"
    if dil:
        d0 = max(dil, key=lambda d: d.obs)
        for x in series:
            if x.kind == "level" and re.match(r"^basic eps$|^basic net (income|loss)[\w ()]* per share$",
                                              (x.name or "").strip(), re.I) \
                    and not getattr(x, "shadowed_by", None) and d0.obs >= x.obs - 1:
                x.shadowed_by = d0.display
                x.shadow_of_code = d0.ltcode
                x.shadow_kind = "basic"
    _basic_pat = r"^basic eps$|^basic net (income|loss)[\w ()]* per share$"
    _free = lambda y: (not getattr(y, "shadowed_by", None)
                       and not getattr(y, "twin_collapsed_by", None))
    for x in series:
        if not (x.kind == "level" and re.match(_basic_pat, (x.name or "").strip(), re.I)
                and _free(x)):
            continue
        order = ([y for y in _cadj] +
                 [y for y in series if y.kind == "level"
                  and re.match(r"^eps \(ex-?\s?1x items?\)$", (y.name or "").strip(), re.I)] +
                 [y for y in series if y.kind == "level"
                  and re.match(r"^(diluted )?eps$", (y.name or "").strip(), re.I)] + dil)
        for y in order:
            if y is x or not _free(y) or y.latest_value is None or y.obs < x.obs - 1:
                continue
            try:
                ok = bool(y.rankable)
            except Exception:
                ok = False
            if ok:
                x.shadowed_by, x.shadow_of_code, x.shadow_kind = y.display, y.ltcode, "basic_eps"
                break


def _shadow_calculated_components(series):
    filed = {m.group(1).lower() for x in series if x.provenance == "filed"
             for m in [re.match(r"^%\s*(volume|price)\s*-\s*total revenue$", (x.name or "").strip(), re.I)] if m}
    for x in series:
        nm = (x.name or "").strip().lower()
        if nm in ("volume", "price") and nm in filed and x.provenance == "derived" \
                and not getattr(x, "shadowed_by", None):
            f = next(y for y in series if re.match(r"^%\s*" + nm + r"\s*-\s*total revenue$",
                                                    (y.name or "").strip(), re.I) and y.provenance == "filed")
            x.shadowed_by, x.shadow_of_code, x.shadow_kind = f.display, f.ltcode, "component"


def _flag_stale_ratios(series):
    by = {x.ltcode: x for x in series}
    for x in series:
        c = str(x.ltcode or "")
        if c.endswith("-M") and by.get(c[:-2]) is not None and by[c[:-2]].repeats_year_ago:
            x.stale = True


def _shadow_exact_twins(series):
    live = [x for x in series if x.latest_value is not None and not getattr(x, "shadowed_by", None)
            and x.kind in ("level", "ratio")]
    by_tail = {}
    for x in live:
        v = x.as_points()
        if len(v) < 8 or v[-1] is None:
            continue
        by_tail.setdefault((x.kind, round(v[-1], 9), round(v[-2] or 0, 9)), []).append(x)
    for grp in by_tail.values():
        if len(grp) < 2:
            continue
        grp.sort(key=lambda x: (0 if x.ltcode == "IS_P_101" else 1,
                                0 if x.provenance == "filed" else 1, len(x.display or ""), x.ltcode or ""))
        keep = grp[0]
        pk = dict(zip(keep.periods, keep.as_points()))
        for y in grp[1:]:
            py = dict(zip(y.periods, y.as_points()))
            shared = [q for q in y.periods if pk.get(q) is not None and py.get(q) is not None][-8:]
            if len(shared) == 8 and y.periods[-1] in shared and all(
                    abs(pk[q] - py[q]) <= 1e-9 * max(abs(pk[q]), 1.0) for q in shared):
                y.shadowed_by = keep.display
                y.shadow_of_code = keep.ltcode
                y.shadow_kind = "twin"


def _name_two_fcfs(series):
    own = [x for x in series if re.search(r"free cash flows? \(non-?gaap\)|co\.?\s*defined fcf|"
                                          r"company[- ]defined (fcf|free cash)", x.name or "", re.I)
           and x.kind == "level"]
    if not own:
        return
    filed = [y for y in series if y.kind == "level" and y.provenance == "filed"
             and re.fullmatch(r"adjusted (fcf|free cash flows?)", (y.name or "").strip(), re.I)]
    for x in list(own):
        if x.provenance == "filed":
            continue
        pa = dict(zip(x.periods, x.as_points()))
        for y in filed:
            pb = dict(zip(y.periods, y.as_points()))
            last = [q for q in x.periods if pa.get(q) is not None and pb.get(q) is not None][-2:]
            if len(last) == 2 and x.periods[-1] in last and all(
                    abs(pa[q] - pb[q]) <= 0.005 * max(abs(pb[q]), 1.0) for q in last):
                x.shadowed_by, x.shadow_of_code, x.shadow_kind = (
                    "Free Cash Flow (company definition)", y.ltcode, "twin")
                y._tier_override = min(x.tier or 3, y.tier or 3)
                own.remove(x)
                own.append(y)
                break
    for x in own:
        x._display_override = "Free Cash Flow (company definition)"
    for x in series:
        if x.kind == "level" and re.fullmatch(r"fcf|free cash flow", (x.name or "").strip(), re.I):
            x._display_override = "Free Cash Flow (OCF less capex)"


def _flag_segment_costs(series):
    by = {(x.name or "").strip().lower(): x for x in series if x.kind == "level"}
    for x in series:
        if x.kind != "level" or " - " in (x.name or "") or not str(x.ltcode or "").startswith("SD_"):
            continue
        nm = (x.name or "").strip().lower()
        stems = [nm]
        _first = re.match(r"([a-z]+)", nm)
        if _first and _first.group(1) != nm:
            stems.append(_first.group(1))
        rev = ebit = None
        for st in stems:
            rev = by.get(st + " - revenue")
            ebit = next((y for k, y in by.items() if re.match(re.escape(st) + r"\s*-\s*ebit\b", k)
                         and "ex-1x" not in k and "ebitda" not in k), None)
            if rev is not None and ebit is not None and rev.periods and rev.periods[-1] == (x.periods or [None])[-1]:
                break
            rev = ebit = None
        if rev is None or ebit is None:
            _rv = by.get(nm + " - revenue")
            _comp = any(k in by for k in ("compensation - " + nm, "allocations - " + nm))
            if _rv is not None and _comp:
                _a, _r = x.as_points()[-4:], _rv.as_points()[-4:]
                if len(_a) == 4 and all(a is not None and r and 0.5 < a / r < 0.99 for a, r in zip(_a, _r)):
                    x._polarity_override = -1
                    x._display_override = (x.display or x.name) + " Segment Costs"
                    x.segment_cost = True
            continue
        pts, pr, pe = x.as_points()[-4:], rev.as_points()[-4:], ebit.as_points()[-4:]
        ok = 0
        for a, r, e in zip(pts, pr, pe):
            if None not in (a, r, e) and r and abs(a - (r - e)) <= 0.015 * abs(r):
                ok += 1
        if ok >= 3:
            x._polarity_override = -1
            x._display_override = (x.display or x.name) + " Segment Costs"
            x.segment_cost = True


def _flag_outside_parts(series):
    _REG = r"(europe|japan|china|row|rest of (?:the )?world|asia(?: pacific)?|latin america|emea|apac|canada)"
    by = {}
    for x in series:
        if x.kind != "pre_growth":
            continue
        m = re.fullmatch(r"(.+?)\s*-\s*(.+)", (x.name or "").strip())
        if not m:
            continue
        by.setdefault(m.group(1).strip().lower(), []).append((m.group(2).strip().lower(), x))
    for x in series:
        if x.kind != "pre_growth":
            continue
        m = re.fullmatch(r"(.+?)\s*-\s*(?:total revenue\s*-\s*)?international", (x.name or "").strip(), re.I)
        if not m or not x.periods or x.values[-1] is None:
            continue
        pre = m.group(1).strip().lower()
        parts = [(sfx, y) for sfx, y in by.get(pre, [])
                 if re.fullmatch(_REG, sfx) and y.periods and y.periods[-1] == x.periods[-1]
                 and y.values[-1] is not None]
        if len(parts) < 3:
            continue
        pv = [y.values[-1] for _, y in parts]
        lo, hi = min(pv), max(pv)
        v = x.values[-1]
        if v < lo - 1.0 or v > hi + 1.0:
            def _p(a):
                if abs(a) < 1e-9:
                    return "0%"
                return ("%+.0f%%" % a) if abs(a - round(a)) < 1e-9 else ("%+.1f%%" % a)
            _info = {"period": x.periods[-1], "value_display": _p(v),
                     "aggregate": x.display,
                     "parts_display": ", ".join(
                         "%s %s" % (y.display if hasattr(y, "display") else y.name, _p(y.values[-1]))
                         for _, y in parts)}
            for _, y in parts:
                y.outside_parts = dict(_info, part=True)


def _flag_vendor_ratio_mismatch(series):
    by = {x.ltcode: x for x in series if x.ltcode}
    levels = [x for x in series if x.kind == "level" and x.ltcode and x.periods]
    for m in series:
        code = m.ltcode or ""
        if not code.endswith("-M") or not m.periods:
            continue
        b = by.get(code[:-2])
        if b is None or not b.periods:
            continue
        pm = {q: v for q, v in zip(m.periods, m.values) if v not in (None, 0)}
        pb = {q: v for q, v in zip(b.periods, b.values) if v not in (None, 0)}
        best = None
        for d in levels:
            if d is b or d is m:
                continue
            pd_ = {q: v for q, v in zip(d.periods, d.values) if v not in (None, 0)}
            qs = [q for q in pm if q in pb and q in pd_]
            if len(qs) < 6:
                continue
            ratio = {q: pm[q] / (pb[q] / pd_[q]) for q in qs}
            k = statistics.median(ratio.values())
            if not any(k > 0 and abs(k / t - 1.0) <= 0.01 for t in (1.0, 100.0)):
                continue
            dev = {q: abs(r / k - 1.0) for q, r in ratio.items()}
            sc = 100.0 if abs(k - 1.0) <= 0.01 and max(abs(v) for v in pm.values()) < 1.5 else 1.0
            near = {q: abs(pm[q] - k * pb[q] / pd_[q]) * sc <= 0.0051 for q in qs}
            ok = [q for q in qs if dev[q] <= 0.015 or near[q]]
            bad = [q for q in qs if dev[q] > 0.10 and not near[q]]
            if len(ok) + len(bad) != len(qs) or len(ok) < 6 or len(bad) > 2:
                continue
            if best is None or len(ok) > best[0]:
                best = (len(ok), d, k, bad, pb, pd_)
        if best is None or not best[3]:
            continue
        _n, d, k, bad, pb_, pd_ = best
        q = max(bad, key=lambda p: (m.periods.index(p)))
        fix = k * pb_[q] / pd_[q]
        _sc = 100.0 if abs(k - 1.0) <= 0.01 and max(abs(v) for v in pm.values()) < 1.5 else 1.0
        m.vendor_ratio_mismatch = {
            "periods": sorted(bad, key=lambda p: m.periods.index(p)),
            "period": q, "vendor": round(pm[q] * _sc, 2), "parts": round(fix * _sc, 2),
            "numerator": b.name, "denominator": d.name,
            "numerator_ltcode": b.ltcode, "denominator_ltcode": d.ltcode}


def _flag_erratic_stocks(series):
    for x in series:
        if x.kind != "level":
            continue
        _stock = re.search(r"portfolio|total assets|outstanding|backlog|\baum\b|"
                           r"deposits|\bloans\b|membership|headcount|square f|sq\.? ?ft",
                           x.name or "", re.I)
        if not _stock or re.search(r"revenue|income|sales|cash flow|net new|additions|starts|"
                                   r"build|provision|\bcf[fio]\b|-\s*cf\b|interest",
                                   x.name or "", re.I):
            continue
        v = [a for a in (x.values or [])[-9:] if a is not None]
        if len(v) < 8 or not v[0]:
            continue
        d = [b - a for a, b in zip(v, v[1:])]
        alt = sum(1 for a, b in zip(d, d[1:]) if a * b < 0
                  and abs(a) > 0.15 * abs(v[0]) and abs(b) > 0.15 * abs(v[0]))
        if alt >= 5:
            x.erratic_stock = True


def _flag_modelled_inputs(series, raw):
    src = {}
    for a in raw.get("actuals") or []:
        c = a.get("LTCODE") or a.get("ltcode")
        if c:
            src.setdefault(c, set()).add(str(a.get("SOURCE_DERIVED") or a.get("source_derived") or "").strip().lower())
    for x in series:
        code = str(x.ltcode or "")
        if not code.endswith("-M"):
            continue
        base = src.get(code[:-2])
        if base and all(MODELLED_SOURCE_PAT.search(b or "") for b in base):
            x.modelled_input = True
    _by_name = {}
    for x in series:
        _by_name.setdefault((x.name or "").strip().lower(), []).append(x)
    for x in series:
        m = re.match(r"^%\s*(m&a|fx|acquisitions?|divestitures?|currency)\b.*?-\s*(.+)$",
                     (x.name or "").strip(), re.I)
        if not m or getattr(x, "modelled_input", False):
            continue
        own = src.get(str(x.ltcode or ""), set())
        if own and not all("calculat" in (b or "") for b in own):
            continue
        tail = m.group(2).strip().lower()
        for sib in ("% organic - ", "% volume - ", "% base business growth - "):
            for y in _by_name.get(sib + tail, []):
                ys = src.get(str(y.ltcode or ""), set())
                if ys and all(MODELLED_SOURCE_PAT.search(b or "") for b in ys):
                    x.modelled_input = True


def _prefer_reported(series):
    filed = [x for x in series if x.provenance == "filed" and x.latest_value is not None]
    for x in series:
        if x.provenance != "derived" or x.latest_value is None or x.kind != "ratio":
            continue
        fam = _family(x.name, {})
        best = None
        for f in filed:
            if f.kind != x.kind or _family(f.name, {}) != fam or f.ltcode == x.ltcode:
                continue
            if f.obs < x.obs - 1:
                continue
            pa, pb = dict(zip(x.periods, x.as_points())), dict(zip(f.periods, f.as_points()))
            shared = [q for q in x.periods if pa.get(q) is not None and pb.get(q) is not None]
            if len(shared) < 8 or x.periods[-1] not in shared:
                continue
            if x.kind == "ratio":
                gaps = sorted(abs(pa[q] - pb[q]) for q in shared)
                tol = 0.5
            else:
                gaps = sorted(abs(pa[q] - pb[q]) / max(abs(pb[q]), 1e-9) * 100.0 for q in shared)
                tol = 1.0
            med = gaps[len(gaps) // 2]
            now = (abs(pa[x.periods[-1]] - pb[x.periods[-1]]) if x.kind == "ratio" else
                   abs(pa[x.periods[-1]] - pb[x.periods[-1]]) / max(abs(pb[x.periods[-1]]), 1e-9) * 100.0)
            if med <= tol and now <= tol and (best is None or med < best[1]):
                best = (f, med, max(gaps), now)
        if best:
            f = best[0]
            x.shadowed_by = f.display
            x.shadow_of_code = f.ltcode
            _lv = x.as_points()[-1]
            f.calculated_twin = {"ltcode": x.ltcode, "name": x.name,
                                 "display": x.display, "latest": _lv,
                                 "latest_display": ("%.2f%%" % _lv) if x.kind == "ratio" else None,
                                 "gap_max": round(best[2], 4), "gap_now": round(best[3], 4),
                                 "gap_bps_max": round(best[2] * 100) if x.kind == "ratio" else None,
                                 "gap_bps_now": round(best[3] * 100) if x.kind == "ratio" else None}


def _income_sign_convention(series):
    for x in series:
        if re.fullmatch(r"\s*(interest|investment|other)\s+income\s*", x.name or "", re.I):
            vv = [v for v in (x.values or [])[-4:] if v is not None]
            if len(vv) >= 2 and all(v < 0 for v in vv):
                return "income_negative"
            if len(vv) >= 2 and all(v > 0 for v in vv):
                return "income_positive"
    return None


def _template_labels(series, template):
    for x in series:
        nm = (x.name or "").strip()
        if template == "reit" and re.match(r"^(diluted )?eps\b|^diluted eps", nm, re.I):
            x._display_override = "Diluted EPS (net earnings)"
        if template == "healthcare":
            _hc = {r"ebit": "Earnings from Operations",
                   r"ebit\s*-\s*adjusted": "Adjusted Earnings from Operations",
                   r"margin\s*-\s*ebit": "Operating Margin",
                   r"margin\s*-\s*ebit\s*-\s*adjusted": "Adjusted Operating Margin"}
            for _pat, _lab in _hc.items():
                if re.fullmatch(_pat, nm, re.I):
                    x._display_override = _lab
            _sg = re.fullmatch(r"(.+?)\s*-\s*ebit(\s*\(ex-?\s?1x items?\))?", nm, re.I)
            if _sg and not re.search(r"margin", nm, re.I):
                x._display_override = (_sg.group(1).strip() + (" Adjusted" if _sg.group(2) else "")
                                       + " Earnings from Operations")
        if template == "machinery" and re.fullmatch(r"backlog", nm, re.I):
            x._display_override = "Order Backlog (total company)"
        _vv = [v for v in (x.values or [])[-4:] if v is not None]
        if _vv and all(v < 0 for v in _vv) and x.kind == "level":
            if re.search(r"-\s*cfi\s*$|investing", nm, re.I) \
                    and not re.search(r"capex|capital expend", nm, re.I):
                x._display_override = (getattr(x, "_display_override", None) or x.display) + " (cash spent)"
            elif re.search(r"income\s*/\s*expense|income\s*&\s*expense|income/\s*expense", nm, re.I):
                _conv = _income_sign_convention(series)
                _base = re.sub(r"\s*income\s*/\s*expenses?|\s*income\s*&\s*expenses?", "",
                               getattr(x, "_display_override", None) or x.display, flags=re.I).strip()
                if _conv == "income_negative":
                    x._display_override = _base + " Income"
                elif _conv == "income_positive":
                    x._display_override = _base + " Expense"
    evidence = []
    by = {x.ltcode: x for x in series}
    rev = by.get("IS_P_101")
    agp = by.get("IS_P_106")
    for x in series:
        if not re.match(r"^\(adj(usted)? gross margin\s*-\s*adj(usted)? opex\)\s*/", x.name or "", re.I):
            continue
        if rev is None or agp is None or not x.values or x.values[-1] is None:
            continue
        for opx in series:
            if not re.search(r"opex|operating expense", opx.name or "", re.I) \
                    or not re.search(r"ipr&?d", opx.name or "", re.I) \
                    or not re.search(r"\bex[-\s]|excl", opx.name or "", re.I):
                continue
            try:
                calc = (agp.values[-1] - opx.values[-1]) / rev.values[-1] * 100.0
            except (TypeError, ZeroDivisionError, IndexError):
                continue
            if abs(calc - x.values[-1]) <= 0.02:
                x._display_override = "Adjusted Operating Margin excl. Acquired IPR&D"
                x._label_proved = True
                evidence.append({"ltcode": x.ltcode, "display": x._display_override,
                                 "text": "%s is adjusted gross profit less %s, over revenue: "
                                         "%.2f%% reproduced from the reported lines"
                                         % (x._display_override, opx.display, calc)})
                break
    stems = {}
    for x in series:
        m = BASIS_SUFFIX_PAT.search(x.name or "")
        if m:
            stems.setdefault(re.sub(r"^%\s*", "", (x.name or "")[:m.start()]).strip().lower(), []).append(x)
    for x in series:
        key = re.sub(r"^%\s*", "", (x.name or "").strip()).lower()
        if key in stems and not BASIS_SUFFIX_PAT.search(x.name or ""):
            _d = getattr(x, "_display_override", None) or x.display
            x._display_override = _d + (", basis not given" if _d.endswith(")")
                                        else " (basis not given)")
    return evidence


def cmd_build(args):
    with open(args.raw, encoding="utf-8") as fh:
        raw = json.load(fh)

    for key in ("ticker", "latest_reported_quarter"):
        if not raw.get(key):
            raise SystemExit(f"raw.json is missing required key {key!r}. Run "
                             f"`compute.py schema` to see the contract.")

    _normalise_period_labels(raw)
    series, aliases, unreadable = _load_series(raw, raw["latest_reported_quarter"].upper())
    _collapse_twins(series)
    resolve_identity_units(series)
    _disambiguate_displays(series)
    attach_segment_scope(series)
    attach_flow_denominators(series)
    detect_signal_roles(series)
    if not series:
        raise SystemExit("raw.json contained no usable actual rows.")

    rev_s0 = _find(series, "IS_P_101", name_pat=r"total revenue|^revenue$|net sales")
    rev_val = rev_s0.latest_value if rev_s0 else None
    routing = resolve_template(raw.get("sector"), [x.name for x in series],
                               cost_names=[x.name for x in series if x.polarity == -1
                                           or re.search(r"\b(cost|costs|expense|expenses|cogs)\b",
                                                        x.name or "", re.I)])
    for x in series:
        x.template = routing["template"]
    _label_evidence = _template_labels(series, routing["template"])
    _detect_splices(series)
    _label_insync_calculated(series)
    _flag_modelled_inputs(series, raw)
    _flag_erratic_stocks(series)
    _flag_outside_parts(series)
    _flag_vendor_ratio_mismatch(series)
    _flag_segment_costs(series)
    _check_revenue_shares(series)
    _name_two_fcfs(series)
    _name_bare_growth_rates(series)
    _prefer_reported(series)
    _eps_basis(series)
    _shadow_exact_twins(series)
    _shadow_calculated_components(series)
    _flag_stale_ratios(series)
    _by = {x.ltcode: x for x in series}
    _alias_stems = set()
    for a in aliases:
        w = _by.get(a.get("duplicate_of"))
        if w is None or "basis" in (w.display or "").lower():
            continue
        _m = BASIS_SUFFIX_PAT.search(a.get("name") or "")
        if _m and not BASIS_SUFFIX_PAT.search(w.name or ""):
            _alias_stems.add((a.get("name") or "")[:_m.start()].strip().lower())
            w._display_override = ((getattr(w, "_display_override", None) or w.display)
                                   + " - " + (a.get("name") or "")[_m.start():].strip(" -"))
            _label_evidence.append({
                "ltcode": w.ltcode,
                "text": "%s carries the %s basis because its figures equal the source's \"%s\" "
                        "line in every quarter scanned"
                        % (w._display_override, (a.get("name") or "")[_m.start():].strip(" -"),
                           a.get("name"))})
            continue
        for word, lab in ((r"\s-\s*cash\s*$|\(cash\)|\bcash basis\b", "cash basis"),
                          (r"\s-\s*net effective\s*$|\(net effective\)|net effective basis",
                           "net effective basis")):
            if lab.split()[0] in (w.display or "").lower().replace("net effective", "net") \
                    if lab == "cash basis" else "net effective" in (w.display or "").lower():
                continue
            if re.search(word, a.get("name") or "", re.I) and not re.search(word, w.name or "", re.I):
                w._display_override = (getattr(w, "_display_override", None) or w.display) + " (" + lab + ")"
                _label_evidence.append({
                    "ltcode": w.ltcode,
                    "text": "%s is labelled %s because its figures equal the source's \"%s\" "
                            "line in every quarter scanned; the line itself states no basis"
                            % (w._display_override, lab, a.get("name"))})
                break
    for x in series:
        if (x.name or "").strip().lower() in _alias_stems and "basis not given" not in (x.display or ""):
            _d = getattr(x, "_display_override", None) or x.display
            x._display_override = _d + (", basis not given" if _d.endswith(")")
                                        else " (basis not given)")
    accel, decel, rankable, share_twins, context, rate_series = _rank(series, rev_val)

    acq = next((x for x in series if re.search(r"acquisition", x.name or "", re.I)
                and not re.search(r"deferred (policy )?acquisition|policy acquisition|"
                                  r"acquisition cap rate|acquisitions?\s*-\s*prologis|"
                                  r"acquisitions?\s*-\s*owned|"
                                  r"content|property|plant|equipment|intangible|software|"
                                  r"real estate|\bppe\b|related (costs|expense)|%|"
                                  r"(subscriber|customer|member) acquisition",
                                  x.name or "", re.I)
                and x.kind == "level"
                and x.periods and x.periods[-1] == str(raw.get("latest_reported_quarter") or "").upper()), None)
    fx = next((x for x in series if re.search(r"^\s*%\s*fx\b", x.name or "", re.I)), None)
    attrib_ctx = {
        "acquisition_material": bool(acq and acq.latest_value and rev_val
                                     and abs(acq.latest_value) >= 0.02 * abs(rev_val)),
        "currency_material": bool(fx and fx.latest_value is not None
                                  and abs(fx.latest_value) >= 1.0),
        "commentary": _load_commentary(getattr(args, "commentary", None)),
    }
    series_by_code = {s.ltcode: s for s in series}

    def _top(rows):
        top, rest = list(rows[:TOP_N]), list(rows[TOP_N:])
        need = sorted([x for x in rest if template_rank(x.template, x.name) is not None
                       and not SEGMENT_DETAIL_PAT.search(x.name or "")
                       and not GEO_CUT_PAT.search(x.name or "")
                       and not getattr(x, "segment_scoped", False)],
                      key=lambda x: (template_rank(x.template, x.name), -x.priority(rev_val)[0]))
        if need:
            for i in range(len(top) - 1, -1, -1):
                if template_rank(top[i].template, top[i].name) is None and top[i].tier >= 3:
                    top[i] = need[0]
                    break
            top.sort(key=lambda x: (-x.priority(rev_val)[0], -abs(x.z), x.ltcode or ""))
        return top
    accel, decel = _top(accel) + [x for x in accel if x not in _top(accel)], \
        _top(decel) + [x for x in decel if x not in _top(decel)]
    acc_rows = [s.row(rev_s0, rev_val, attrib_ctx) for s in accel[:TOP_N]]
    dec_rows = [s.row(rev_s0, rev_val, attrib_ctx) for s in decel[:TOP_N]]
    ctx_rows = [s.row(rev_s0, rev_val, attrib_ctx) for s in context]
    rate_rows = [s.row(rev_s0, rev_val, attrib_ctx) for s in rate_series]
    for _s, _r in zip(rate_series, rate_rows):
        _r["rate_primary"] = bool(template_rank(_s.template, _s.name) is not None
                                  and not SEGMENT_DETAIL_PAT.search(_s.name or "")
                                  and not getattr(_s, "segment_scoped", False))
    excluded = [{
        "ltcode": s.ltcode, "name": s.name, "quarters": s.obs,
        "reason": _exclusion_reason(s),
        "reason_code": _exclusion_reason_code(s),
    } for s in series if not s.rankable]
    excluded += [{"ltcode": a["ltcode"], "name": a["name"], "quarters": 0,
                  "reason": a.get("reason_long") or a.get("reason")
                            or ("duplicate of " + a["duplicate_of"]),
                  "reason_code": "duplicate",
                  "duplicate_of": a["duplicate_of"], "twin_of": a["duplicate_of"],
                  "provenance": a.get("provenance"),
                  "twin_provenance": a.get("twin_provenance")} for a in aliases]
    excluded += [{"ltcode": u["ltcode"], "name": u["name"], "quarters": 0,
                  "reason": "returned by the scan and unreadable: no value "
                            "parsed from any of %d observations (e.g. %s)"
                            % (u["rows"], ", ".join(u["samples"]) or "blank"),
                  "unparsed": True, "samples": u["samples"], "reason_code": "unparsed"}
                 for u in unreadable]
    by_code = {s.ltcode: s for s in series}
    excluded += [{"ltcode": c, "name": by_code[c].name if c in by_code else c,
                  "quarters": by_code[c].obs if c in by_code else 0,
                  "reason": "derived twin of " + w, "reason_code": "derived_twin"}
                 for c, w in sorted(share_twins.items())]

    retrieved_codes = sorted(
        {r.get("ltcode") or r.get("LTCODE") for r in (raw.get("actuals") or [])
         if (r.get("ltcode") or r.get("LTCODE"))})
    series_scanned = len(series) + len(aliases) + len(unreadable)
    series_ranked = series_scanned - len(excluded)

    quarters = max((s.obs for s in series), default=0)
    payload = {
        "schema": "inflection-monitor/1.0",
        "engine_version": VERSION,
        "generated_utc": (os.environ.get("INFLECTION_GENERATED_UTC")
                          or _utc_now().strftime("%Y-%m-%dT%H:%M:%SZ")),
        "ticker": raw["ticker"].upper(),
        "company": raw.get("company"),
        "ticker_id": raw.get("ticker_id"),
        "latest_quarter": raw["latest_reported_quarter"].upper(),
        "report_date": raw.get("report_date"),
        "sector": raw.get("sector"),
        "sector_routing": routing,
        "retrieved_codes": retrieved_codes,
        "ranked_codes": sorted(x.ltcode for x in series if x.rankable),
        "scored_codes": sorted(x.ltcode for x in series),
        "scan_period": {},
        "coverage": {
            "series_scanned": series_scanned,
            "series_ranked": series_ranked,
            "series_retrieved": len(retrieved_codes),
            "series_excluded": len(excluded),
            "series_planned": raw.get("codes_planned"),
            "displayed": len(acc_rows) + len(dec_rows) + len(ctx_rows) + len(rate_rows),
            "material_rate": sum(1 for x in rate_series if x.material_inflection),
            "quarters": quarters,
            "material_accelerating": len(accel),
            "material_decelerating": len(decel),
            "material_total": len(accel) + len(decel) + sum(1 for x in rate_series
                                                            if x.material_inflection
                                                            and x not in accel + decel),
            "primary_context": len(ctx_rows),
            "quiet_series": sum(1 for s in rankable
                                if not s.qualifies),
        },
        "scan_status": _scan_status(raw, series, quarters),
        "implication_ledger": [
            {"population": ("primary_context" if s0 in context
                            else "material" if (s0 in accel + decel or s0.material_inflection)
                            else "rate_context"),
             "ltcode": s0.ltcode, "metric": s0.display,
             "implication": s0.implication(rev_s0)[0]
             if isinstance(s0.implication(rev_s0), tuple) else s0.implication(rev_s0),
             "tier": s0.tier_with(rev_s0.latest_value if rev_s0 else None),
             "growth": s0.latest_growth, "accel": s0.latest_accel,
             "z": _clamp_display(s0.z), "confidence": s0.confidence,
             "displayed_in_ranked_table": s0.ltcode in {
                 r["ltcode"] for r in (acc_rows + dec_rows)}}
            for s0 in (accel + decel + context
                       + [x for x in rate_series if x not in accel + decel + context])],
        "accelerating": acc_rows,
        "rate_kpis": rate_rows,
        "decelerating": dec_rows,
        "primary_context": ctx_rows,
        "excluded": sorted(excluded, key=lambda r: r["ltcode"] or ""),
        "confirmations": _confirmations(series),
        "divergences": _divergences(series, raw.get("sector"),
                                     (routing or {}).get("template")),
        "constants": _constants(),
    }
    _curs = {}
    for _s in series:
        _c = getattr(_s, "currency", None)
        if _c:
            _curs[_c] = _curs.get(_c, 0) + 1
    _dom = max(_curs.items(), key=lambda kv: kv[1])[0] if _curs else None
    _DEFAULT_CUR["sym"] = _dom
    payload["reporting_currency"] = {
        "symbol": _dom,
        "lines_quoted": _curs.get(_dom, 0),
        "mixed": sorted(k for k in _curs if k != _dom) or None,
        "conflicted_lines": sorted(
            _s.name for _s in series if getattr(_s, "currency_conflict", False))[:6],
    }
    payload["deep_dive"] = _pick_deep_dives(acc_rows + dec_rows + ctx_rows
                                            + [r for r in rate_rows if r.get("z") is not None
                                               and abs(r["z"]) >= MATERIAL_Z])

    ranked_codes = {r["ltcode"] for r in acc_rows + dec_rows}
    quiet = [s for s in rankable
             if s.ltcode not in ranked_codes and s.tier == 1
             and not s.qualifies
             and s.ltcode not in {r["ltcode"] for r in rate_rows}]
    payload["quiet_headline"] = [{
        "ltcode": s.ltcode, "name": s.name, "display": s.display, "unit": s.unit,
        "kind": s.kind,
        "latest_value": s.latest_value, "latest_growth": s.latest_growth,
        "latest_accel": s.latest_accel, "z_display": _clamp_display(s.z),
        "prev_growth": s.prev_growth,
        "prev_period": s.periods[-2] if len(s.periods) >= 2 else None,
        "streak": s.streak, "streak_dir": s.streak_dir,
        "flow_rate_mode": bool(getattr(s, "flow_rate_mode", False)),
        "flow_denominator_name": getattr(s, "flow_denominator_name", None),
    } for s in sorted(quiet, key=lambda x: (
        (template_rank((routing or {}).get("template"), x.name) if routing else None)
        if (routing and template_rank((routing or {}).get("template"), x.name) is not None)
        else 99,
        -abs(x.z or 0), x.ltcode))[:QUIET_ROSTER_N]]

    _scored = {s.ltcode for s in rankable}
    payload["unscoreable_primary"] = [
        {"ltcode": s.ltcode, "name": s.name, "display": s.display,
         "quarters": s.obs,
         "reason": _exclusion_reason(s),
         "reason_code": _exclusion_reason_code(s)}
        for s in sorted(series, key=lambda x: x.ltcode)
        if s.ltcode not in _scored and s.tier == 1 and s.obs >= 2
        and not s.modelled and not getattr(s, "step_like", False)
    ][:6]

    rev_s = _find(series, "IS_P_101", name_pat=r"total revenue|^revenue$|net sales")
    rev_v = rev_s.latest_value if rev_s else None
    rev_start = rev_s.streak_start_idx if rev_s else None
    for r in acc_rows + dec_rows + payload["deep_dive"]:
        s_obj = series_by_code.get(r["ltcode"])
        if s_obj is None:
            continue
        comparable = (
            s_obj.kind == "level"
            and getattr(s_obj, "unit_label", None) == "currency"
            and rev_s is not None
            and getattr(rev_s, "unit_label", None) == "currency"
            and not PER_SHARE_PAT.search(s_obj.name or "")
            and not re.search(r"per share|/\s*share|\bper unit\b|\bper boe\b",
                              s_obj.name or "", re.I))
        if (rev_v and comparable and s_obj.category in ("IS", "CF", "SD")
                and s_obj.latest_value is not None
                and abs(s_obj.latest_value) < 0.01 * abs(rev_v)):
            r["small_line"] = True
        if (rev_start is not None and s_obj.streak_start_idx is not None
                and s_obj.ltcode != (rev_s.ltcode if rev_s else None) and s_obj.streak >= 2):
            lead = rev_start - s_obj.streak_start_idx
            if lead > 0:
                r["lead_quarters"] = lead

    all_periods = [pp for sx in series for pp in (sx.periods or [])]
    if all_periods:
        payload["scan_period"] = {"first": series[0].periods[0] if series[0].periods else None,
                                  "last": payload["latest_quarter"]}
        longest = max(series, key=lambda x: len(x.periods or []))
        payload["scan_period"]["first"] = (longest.periods or [None])[0]


    na, nd = len(acc_rows), len(dec_rows)
    if na == 0 and nd == 0:
        direction = "No material inflection"
    elif nd == 0:
        direction = "Accelerating"
    elif na == 0:
        direction = "Decelerating"
    elif na >= 2 * nd:
        direction = "Accelerating"
    elif nd >= 2 * na:
        direction = "Decelerating"
    else:
        direction = "Mixed"
    payload["direction"] = direction
    payload["direction_counts"] = {"accelerating": na, "decelerating": nd}

    def _tally(rows):
        t = {"Favourable": 0, "Unfavourable": 0, "Mixed": 0, "Ambiguous": 0}
        for r in rows:
            if r.get("implication") in t:
                t[r["implication"]] += 1
        return t

    all_material = [s_.row(rev_s0, rev_val, attrib_ctx)
                    for s_ in accel + decel + [x for x in rate_series if x.material_inflection
                                               and x not in accel + decel]]
    tally = _tally(all_material)
    _mat = accel + decel + [x for x in rate_series
                            if x.material_inflection and x not in accel + decel]
    _n_acc = sum(1 for x in _mat if (x.latest_accel or 0) > 0)
    payload["published_counts"] = {
        "population": "lines that cleared both materiality bars, rate KPIs included",
        "total": len(_mat),
        "accelerating": _n_acc,
        "decelerating": len(_mat) - _n_acc,
        "implication": {k: v for k, v in tally.items()},
        "displayed": {"accelerating": len(acc_rows), "decelerating": len(dec_rows)},
    }
    payload["implication_counts_context"] = _tally(ctx_rows)
    payload["context_implications"] = [
        {"metric": r.get("display") or r["name"], "ltcode": r["ltcode"],
         "implication": r.get("implication"), "z_display": r.get("z_display"),
         "latest_accel": r.get("latest_accel"), "unit": r.get("unit")}
        for r in ctx_rows]
    fav, unfav = tally["Favourable"], tally["Unfavourable"]
    if fav == 0 and unfav == 0:
        balance = "No clear implication"
    elif unfav == 0:
        balance = "Favourable"
    elif fav == 0:
        balance = "Unfavourable"
    elif fav >= 2 * unfav:
        balance = "Favourable"
    elif unfav >= 2 * fav:
        balance = "Unfavourable"
    else:
        balance = "Mixed"
    unclear = tally["Mixed"] + tally["Ambiguous"]
    if balance in ("Favourable", "Unfavourable") and max(fav, unfav) < unclear:
        balance = "Mostly unclear, leaning " + balance.lower()

    bl = balance.lower()
    opposing = ("Favourable" if bl.endswith("unfavourable")
                else "Unfavourable" if bl.endswith("favourable") else None)
    ctx_against = [r for r in ctx_rows
                   if opposing and r.get("implication") == opposing
                   and (r.get("tier") or 9) <= 2]
    payload["context_opposes"] = [
        {"metric": r.get("display") or r["name"], "ltcode": r["ltcode"],
         "implication": r.get("implication"), "tier": r.get("tier")}
        for r in ctx_against]
    payload["implication_balance"] = balance
    payload["implication_counts"] = tally
    payload["direction_and_implication"] = (
        direction if direction == "No material inflection"
        else (f"{direction} on pace, no clear implication either way"
              if balance == "No clear implication"
              else f"{direction} on pace, {balance.lower()} on implication"))

    _key = lambda r: (-(r.get("priority") or 0), -abs(r["z"]), r["ltcode"] or "")
    _small_seg = set()
    for _r in acc_rows + dec_rows + ctx_rows:
        _m = re.match(r"^(.+?)\s+-\s+(.+)$", _r.get("name") or "")
        if not _m or _r.get("kind") != "level" or not _r.get("latest_value"):
            continue
        _meas = _m.group(2).strip().lower()
        for _x in series_by_code.values():
            if _x.kind == "level" and _x.latest_value and re.match(r"^total\b", _x.name or "", re.I) \
                    and _meas in (_x.name or "").lower() \
                    and abs(_x.latest_value) >= 20 * abs(_r["latest_value"]):
                _small_seg.add(_r.get("ltcode"))
                break
    _elig = [r for r in acc_rows + dec_rows + ctx_rows if front_page_eligible(r)
             and r.get("ltcode") not in _small_seg]
    if not _elig:
        _elig = acc_rows + dec_rows + ctx_rows
    _elig = _family_representatives(_elig)
    _held = [r["ltcode"] for r in acc_rows + dec_rows + ctx_rows if r not in _elig]
    _material = sorted([r for r in acc_rows + dec_rows if r in _elig], key=_key)
    _picked, _ctx_used = [], 0
    _fam_used, _ratios = {}, 0
    def _rrank(r):
        tr = _row_template_rank(r)
        return (0 if tr is not None else 1, tr if tr is not None else 99,
                -(r.get("priority") or 0), r.get("ltcode") or "")
    _allowed_ratios = {r.get("ltcode") for r in
                       sorted([r for r in _elig if r.get("kind") == "ratio"],
                              key=_rrank)[:HEADLINE_MAX_RATIOS]}
    def _slot_ok(r):
        fam = _family(r["name"], r)
        if _fam_used.get(fam, 0) >= HEADLINE_MAX_PER_FAMILY:
            return False
        if r.get("kind") == "ratio" and (_ratios >= HEADLINE_MAX_RATIOS
                                         or r.get("ltcode") not in _allowed_ratios):
            return False
        return True
    def _take(r):
        nonlocal _ratios
        fam = _family(r["name"], r)
        _fam_used[fam] = _fam_used.get(fam, 0) + 1
        if r.get("kind") == "ratio":
            _ratios += 1
        _picked.append(r)
    for r in sorted(_elig, key=_key):
        is_ctx = (r.get("qualified_by") or "").startswith("primary context")
        if is_ctx and _ctx_used >= MAX_CONTEXT_IN_TOP:
            continue
        if not _slot_ok(r):
            continue
        _take(r)
        _ctx_used += 1 if is_ctx else 0
        if len(_picked) == TOP_N:
            break
    for r in _material:
        if len(_picked) == TOP_N:
            break
        if r not in _picked and _slot_ok(r):
            _take(r)
    _picked.sort(key=_key)
    payload["headline_moves"] = _picked[:TOP_N]
    payload["headline_held_back"] = _held

    _profit_up = any(
        re.search(r"margin|ebitda|ebit\b|earnings|\beps\b|net income|operating income",
                  str(r.get("display") or r.get("name") or ""), re.I)
        for r in acc_rows)
    payload["cash_conversion"] = _cash_conversion(series, rev_s0, _profit_up)
    _attach_bridges(payload, series_by_code)
    payload["company_exclusions"] = company_exclusions(list(series_by_code.values()))
    _attach_adjusted_twins(payload, series_by_code, raw)
    _attach_exclusion_twins(payload, list(series_by_code.values()))
    _attach_revenue_decomposition(payload, series_by_code)
    _attach_reported_twins(payload, series_by_code)
    payload["source_corrections"] = _swap_component_labels(raw)
    payload["guidance_check"] = _guidance(raw, series_by_code, payload["latest_quarter"])
    for _bk in ("scored", "delivered", "other_horizon", "forward_uncompared",
                "delivered_uncompared", "revisions"):
        for _gr in (payload["guidance_check"].get(_bk) or []):
            if str(_gr.get("display") or "").startswith("InSync-Calculated "):
                _gr["display"] = _gr["display"][len("InSync-Calculated "):]
            if routing.get("template") == "healthcare":
                _d = str(_gr.get("display") or _gr.get("name") or "")
                _sg = re.fullmatch(r"(.+?)\s*-\s*ebit\s*(\((?:ex-?\s?1x items?|adj\.?)\))?", _d, re.I)
                if _sg and not re.search(r"margin", _d, re.I):
                    _gr["display"] = (_sg.group(1).strip() + (" Adjusted" if _sg.group(2) else "")
                                      + " Earnings from Operations")
    _notes = [o for o in (raw.get("outlook") or [])
              if re.search(r"(\u00b1|\+/-)\s*\d+(\.\d+)?\s*bps", str(o.get("GUIDE_TEXT") or ""), re.I)]
    for _bk in ("other_horizon", "revisions"):
        for _gr in (payload["guidance_check"].get(_bk) or []):
            _disp = str(_gr.get("display") or "")
            _words = [w for w in re.findall(r"[a-z]{4,}", _disp.lower()) if w not in ("total", "adjusted")]
            if not _words:
                continue
            _sp = str(_gr.get("source_period") or _gr.get("latest_source_period") or "").upper()
            for _o in _notes:
                _tx = str(_o.get("GUIDE_TEXT") or "")
                if str(_o.get("FISCALPERIOD") or "") != str(_gr.get("target_period") or "") \
                        or str(_o.get("SOURCE_PERIOD") or "").upper() != _sp \
                        or not all(w in _tx.lower() for w in _words):
                    continue
                _m = re.search(r"(?:\u00b1|\+/-)\s*(\d+(?:\.\d+)?)\s*bps", _tx, re.I)
                _key = "guide_display_text" if _bk == "other_horizon" else "latest_display"
                if _m and _gr.get(_key) and " to " not in str(_gr[_key]) and "\u00b1" not in str(_gr[_key]):
                    _gr[_key] = str(_gr[_key]) + " \u00b1 " + _m.group(1) + " bps"
                    _gr["tolerance_source"] = "company outlook note: " + _tx.strip()
                break
    for _gr in (payload["guidance_check"].get("forward_uncompared") or []):
        _sx = series_by_code.get(_gr.get("ltcode"))
        if _gr.get("latest_growth") is None and _sx is not None and _guide_growth(_sx) is not None \
                and _sx.periods and _sx.periods[-1] == payload["latest_quarter"]:
            _gr["latest_growth"] = _sx.latest_growth
            _gr.setdefault("unit", _sx.unit)
    for _bk in ("scored", "delivered", "other_horizon", "forward_uncompared", "delivered_uncompared"):
        for _gr in (payload["guidance_check"].get(_bk) or []):
            _t = str(_gr.get("guide_display_text") or "")
            _pm = re.search(r"\+/-\s*([\d.]+)\s*(%?)", str(_gr.get("guide_text") or ""))
            if _pm and _t and "\u00b1" not in _t and " to " not in _t:
                _gr["guide_display_text"] = _t = _t + " \u00b1 " + _pm.group(1) + _pm.group(2)
            _gt = str(_gr.get("guide_text") or "")
            if _t and not re.match(r"^(approx\.|above\b|at least\b|up to\b)", _t, re.I):
                if re.search(r"at ?least|minimum of", _gt, re.I):
                    _gr["guide_display_text"] = _t = "at least " + _t
                elif re.search(r"greater than|more than|in excess of|^\s*>|\bexceed", _gt, re.I):
                    _gr["guide_display_text"] = _t = "above " + _t
                elif re.search(r"\bapprox|\baround\b|\babout\b|~", _gt, re.I) and " to " not in _t:
                    _gr["guide_display_text"] = _t = "approx. " + _t
            if _t.startswith("-") and re.search(r"capex|capital expend",
                                                str(_gr.get("name") or _gr.get("display") or ""), re.I):
                _parts = [x.strip().lstrip("-") for x in _t.split(" to ")]
                _gr["guide_display_text"] = " to ".join(reversed(_parts)) if len(_parts) == 2 else _parts[0]
    _CNT = re.compile(r"\bstores?\b|\blocations?\b|\brestaurants?\b|\bbranches\b|"
                      r"headcount|employees|\bunits?\b(?! revenue)|\bcount\b|"
                      r"\bhomes?\b|\blots\b|\bclosed\b|deliveries|communit", re.I)
    _MON = re.compile(r"revenue|sales(?! -)|income|cost|expense|capex|capital|\$\s*/|per store|"
                      r"/\s*store|margin|price|value|\basp\b", re.I)

    def _count_text(t):
        t = re.sub(r"\$\s*(\d[\d,]*)\.00\b", r"\1", str(t))
        t = re.sub(r"(?<![\d.])(\d[\d,]*)\.0+(?![\d%])", r"\1", t)
        t = re.sub(r"\$\s*(\d)", r"\1", t)
        t = re.sub(r"\b(\d+(?:\.\d+)?)K\b",
                   lambda m: "{:,}".format(int(round(float(m.group(1)) * 1000))), t)
        return re.sub(r"\s+count\b", "", t)

    _BAND = re.compile(r"\b((?:low|mid|high)(?:[- ]to[- ](?:low|mid|high))?)[- ]+(\d)0(?:\.0+)?\s*%", re.I)

    def _band_text(t):
        return _BAND.sub(lambda m: m.group(1).lower() + " " + m.group(2) + "0s%", str(t))
    for _bk in ("scored", "delivered", "other_horizon", "forward_uncompared",
                "delivered_uncompared", "revisions"):
        for _gr in (payload["guidance_check"].get(_bk) or []):
            if not _gr.get("guide_display_text") and _BAND.search(str(_gr.get("guide_text") or "")):
                _gr["guide_display_text"] = _band_text(str(_gr["guide_text"]).strip())
            for _key in ("guide_display_text", "latest_display", "first_display",
                         "latest_text", "first_text"):
                if _gr.get(_key) and _BAND.search(str(_gr[_key])):
                    _gr[_key] = _band_text(_gr[_key])
    _VOL = re.compile(r"\((mwh?|gwh?)\)|\b(mwh?|gwh?)\b", re.I)

    def _vol_text(t, unit):
        t = _count_text(t)
        return t if re.search(r"\b" + unit + r"\b", t, re.I) else t + " " + unit
    def _bps_text(t):
        t = str(t or "")
        nums = re.findall(r"\d+(?:\.\d+)?", t)
        if not nums or len(nums) > 2:
            return None
        neg = re.search(r"below|lower|\bdown\b|declin|contract|decreas|compress|^\s*[-(]", t, re.I)
        sg = "-" if neg else "+"
        return " to ".join(sg + n + " bps" for n in nums)
    _rv_list = payload["guidance_check"].setdefault("revisions", [])
    _have_rv = {(r.get("ltcode"), str(r.get("target_period"))) for r in _rv_list}
    for _gr in list(payload["guidance_check"].get("other_horizon") or []):
        _c = str(_gr.get("ltcode") or "")
        if not _c.endswith("-BY") or (_c, str(_gr.get("target_period"))) in _have_rv:
            continue
        _prior = [g for g in (raw.get("guidance") or [])
                  if str(g.get("LTCODE") or "") == _c[:-3] + "-M"
                  and str(g.get("FISCALPERIOD") or "") == str(_gr.get("target_period"))
                  and _quarter_sort_key(g.get("SOURCE_PERIOD")) < _quarter_sort_key(_gr.get("source_period"))
                  and re.search(r"consistent|in line|flat|similar|unchanged", str(g.get("GUIDE_TEXT") or ""), re.I)]
        _t = str(_gr.get("guide_text") or "")
        _n = re.search(r"(\d+(?:\.\d+)?)", _t)
        if not _prior or not _n:
            continue
        _neg = re.search(r"below|lower|\bdown\b|declin|contract|decreas|compress", _t, re.I)
        _p0 = max(_prior, key=lambda g: _quarter_sort_key(g.get("SOURCE_PERIOD")))
        _rv_list.append({"ltcode": _c, "name": _gr.get("name"), "display": _gr.get("display"),
                         "target_period": _gr.get("target_period"),
                         "direction": "lowered" if _neg else "raised",
                         "changed_in": _gr.get("source_period"),
                         "first_display": "in line with the prior year", "first_text": _p0.get("GUIDE_TEXT"),
                         "latest_text": _t, "latest_display": None,
                         "first_source_period": _p0.get("SOURCE_PERIOD"),
                         "latest_source_period": _gr.get("source_period"),
                         "is_percentage": False, "midpoint_change_bps": None, "midpoint_change_pct": None,
                         "worded": False, "revisions": 1})
    for _bk in ("scored", "delivered", "other_horizon", "forward_uncompared",
                "delivered_uncompared", "revisions"):
        for _gr in (payload["guidance_check"].get(_bk) or []):
            if not re.search(r"^change in bps\b", str(_gr.get("name") or _gr.get("display") or ""), re.I):
                continue
            for _key, _src in (("guide_display_text", "guide_text"), ("latest_display", "latest_text"),
                               ("first_display", "first_text")):
                _b = _bps_text(_gr.get(_src)) if _gr.get(_src) else None
                if _b:
                    _gr[_key] = _b
    for _bk in ("scored", "delivered", "other_horizon", "forward_uncompared",
                "delivered_uncompared", "revisions"):
        for _gr in (payload["guidance_check"].get(_bk) or []):
            _nm = str(_gr.get("display") or _gr.get("name") or "")
            _vm = _VOL.search(_nm)
            if _vm:
                _unit = (_vm.group(1) or _vm.group(2)).upper().replace("H", "h")
                for _key in ("guide_display_text", "latest_display", "first_display",
                             "actual_display", "versus_display"):
                    if _gr.get(_key) and re.search(r"\d", str(_gr[_key])):
                        _gr[_key] = _vol_text(_gr[_key], _unit)
                continue
            if _CNT.search(_nm) and not _MON.search(_nm):
                for _key in ("guide_display_text", "latest_display", "first_display",
                             "actual_display", "versus_display"):
                    if _gr.get(_key):
                        _gr[_key] = _count_text(_gr[_key])
    for _bk in ("scored", "delivered", "other_horizon", "forward_uncompared",
                "delivered_uncompared", "revisions"):
        for _gr in (payload["guidance_check"].get(_bk) or []):
            if str(_gr.get("display") or "").endswith(" (OCF less capex)"):
                _gr["display"] = "Free Cash Flow (company definition)"
    for _bk in ("scored", "delivered", "other_horizon", "forward_uncompared",
                "delivered_uncompared", "revisions"):
        for _gr in (payload["guidance_check"].get(_bk) or []):
            if re.search(r"\(ex-?\s?1x", str(_gr.get("display") or ""), re.I):
                _dn = display_name(str(_gr.get("name") or ""))
                if isinstance(_dn, str) and not re.search(r"\(ex-?\s?1x", _dn, re.I):
                    _gr["display"] = _dn
    def _mid(t):
        xs = [float(x.replace(",", "")) for x in re.findall(r"-?\d[\d,]*\.?\d*", str(t or ""))]
        return (sum(xs[:2]) / len(xs[:2])) if xs else None
    _gcx = payload["guidance_check"]
    _pool = [g for _bk in ("other_horizon", "forward_uncompared", "scored", "revisions")
             for g in (_gcx.get(_bk) or [])]
    for _rv in (_gcx.get("revisions") or []):
        if not re.fullmatch(r"(reported |diluted )?eps", str(_rv.get("name") or "").strip(), re.I):
            continue
        _first = re.sub(r"\s+", "", str(_rv.get("first_display") or _rv.get("first_text") or "")).lower()
        _sp = _rv.get("latest_source_period") or _rv.get("changed_in")
        for _a in _pool:
            if _a is _rv or not re.search(r"eps \(ex-?\s?1x|adjusted (diluted )?eps", str(_a.get("name") or ""), re.I):
                continue
            if str(_a.get("target_period")) != str(_rv.get("target_period")):
                continue
            _at = _a.get("guide_display_text") or _a.get("latest_display") or _a.get("guide_text")
            if re.sub(r"\s+", "", str(_at or "")).lower() != _first:
                continue
            if (_a.get("source_period") or _a.get("latest_source_period")) != _sp:
                continue
            _m1, _m0 = _mid(_rv.get("latest_display")), _mid(_at)
            _rv["items_only"] = {"adjusted_display": _a.get("display") or display_name(_a.get("name") or ""),
                                 "adjusted_guide": _at,
                                 "gap_display": ("$%.2f" % abs(_m0 - _m1)) if (_m0 is not None and _m1 is not None
                                                                              and "$" in str(_at)) else None}
            break
    for _bk in ("other_horizon", "forward_uncompared", "scored", "delivered"):
        _rows = payload["guidance_check"].get(_bk) or []
        _best = {}
        for _gr in _rows:
            _k = (_gr.get("display"), _gr.get("target_period"),
                  _gr.get("guide_display_text") or _gr.get("guide_text"))
            _o = _best.get(_k)
            if _o is None or _quarter_sort_key(_gr.get("source_period") or "") > \
                    _quarter_sort_key(_o.get("source_period") or ""):
                _best[_k] = _gr
        payload["guidance_check"][_bk] = [g for g in _rows if _best.get(
            (g.get("display"), g.get("target_period"),
             g.get("guide_display_text") or g.get("guide_text"))) is g]
    _oh = payload["guidance_check"].get("other_horizon") or []
    _latest = {}
    for _g in _oh:
        _k = (str(_g.get("ltcode") or "").split("-")[0], str(_g.get("target_period")))
        _q = _quarter_sort_key(_g.get("source_period") or "")
        if _q > _latest.get(_k, (-9, -9)):
            _latest[_k] = _q
    payload["guidance_check"]["other_horizon"] = [
        _g for _g in _oh
        if _quarter_sort_key(_g.get("source_period") or "") ==
        _latest.get((str(_g.get("ltcode") or "").split("-")[0], str(_g.get("target_period"))))]
    payload["label_evidence"] = _label_evidence
    payload["one_time_items"] = _one_time_items(series, payload["latest_quarter"])
    _nio = next((x for x in series if _is_non_operating_line(x)
                 and x.periods and x.periods[-1] == payload["latest_quarter"]
                 and x.latest_value is not None), None)
    _ebit = next((x for x in series if x.ltcode in ("IS_P_109", "IS_P_110") and x.latest_value), None)
    _adj_nio = next((x for x in series if re.match(r"net interest\s*&\s*other income\s*/\s*expenses?\s*\(ex-?\s?1x",
                                                   (x.name or "").strip(), re.I)
                     and x.periods and x.periods[-1] == payload["latest_quarter"]), None)
    payload["non_operating"] = None
    if _nio is not None and _ebit is not None and abs(_nio.latest_value) >= 0.10 * abs(_ebit.latest_value):
        _cur = getattr(_nio, "currency", None) or _DEFAULT_CUR.get("sym")
        payload["non_operating"] = {
            "display": _non_operating_word(series, _nio, payload["latest_quarter"]),
            "value_display": _money(abs(_nio.latest_value), _cur),
            "adjusted_value_display": (_money(abs(_adj_nio.latest_value), _cur)
                                       if _adj_nio is not None and _adj_nio.latest_value is not None else None),
            "period": payload["latest_quarter"]}
    payload["non_operating_window"] = _non_operating_window(series, payload)
    _inc = next((x for x in series if re.search(r"strategic capital income \(incl\. promote", x.name or "", re.I)
                 and x.periods and x.periods[-1] == payload["latest_quarter"]), None)
    _exc = next((x for x in series if re.search(r"strategic capital income \(excl\. promote", x.name or "", re.I)
                 and x.periods and x.periods[-1] == payload["latest_quarter"]), None)
    if _inc is not None and _exc is not None and _inc.latest_value and _exc.latest_value:
        _prom = _inc.latest_value - _exc.latest_value
        _cur = getattr(_inc, "currency", None) or _DEFAULT_CUR.get("sym")
        if _prom > 0:
            for _k in ("accelerating", "decelerating", "primary_context", "headline_moves", "deep_dive"):
                for _r in (payload.get(_k) or []):
                    if not re.search(r"strategic capital segment$", str(_r.get("name") or ""), re.I) \
                            or re.search(r"^total", str(_r.get("name") or ""), re.I):
                        continue
                    _vs = [v for v in (_r.get("value_series") or [])]
                    if len(_vs) < 5 or _vs[-1] is None or _vs[-5] is None:
                        continue
                    _inc_yy = _vs[-1] - _vs[-5]
                    if _inc_yy > 0 and abs(_inc_yy - _prom) <= 0.25 * _prom:
                        _r["promote_match"] = {"increase_display": _money(_inc_yy, _cur),
                                               "promote_display": _money(_prom, _cur),
                                               "period": payload["latest_quarter"]}
    _qe = collections.Counter(
        str(r.get("QUARTERENDDATE") or r.get("quarterenddate") or "")[:10]
        for r in (raw.get("actuals") or [])
        if clean_period_label(r.get("FISCALPERIOD") or r.get("fiscal_period")).upper()
        == payload["latest_quarter"])
    _qe.pop("", None)
    payload["latest_quarter_end"] = (_qe.most_common(1)[0][0] if _qe else None)
    _ed, _edb = raw.get("report_date"), "source report date"
    if not _ed:
        _gd = sorted({str(g.get("GUIDEDATE") or g.get("guide_date") or "")[:10]
                      for g in (raw.get("guidance") or [])
                      if str(g.get("SOURCE_PERIOD") or g.get("source_period") or "").upper()
                      == str(payload["latest_quarter"]).upper()} - {""})
        _ed, _edb = (_gd[0], "date of the guidance issued with the quarter") if _gd else (None, None)
    payload["earnings_date"] = str(_ed)[:10] if _ed else None
    payload["earnings_date_basis"] = _edb if _ed else None
    payload["comparison_basis"] = {
        "year_ago": _year_ago_label(payload["latest_quarter"]),
        "last_quarter": _prev_quarter_label(payload["latest_quarter"])}
    payload["quarter_strip"] = _quarter_strip(
        series, rev_s0, rev_val, attrib_ctx, routing, payload["guidance_check"],
        payload.get("company_exclusions") or [])
    for _c in ((payload.get("quarter_strip") or {}).get("cells") or []):
        if payload.get("non_operating_window") and _below_ebit_row(_c):
            _c["non_operating_window"] = payload["non_operating_window"]
    hm_rows = sorted(acc_rows + dec_rows + [r for r in rate_rows if r.get("z") is not None
                                            and abs(r["z"]) >= MATERIAL_Z],
                     key=lambda r: (-(r.get("priority") or 0), -abs(r["z"])))[:HEATMAP_ROWS]
    payload["heatmap"] = _heatmap(hm_rows, series_by_code)

    _dive_codes = [r["ltcode"] for r in payload["deep_dive"]]
    _monitor_pool = _drop_redundant_derived(
        list({r["ltcode"]: r for r in
              (payload["headline_moves"] + acc_rows + dec_rows
               + [r for r in rate_rows if r.get("z") is not None
                  and abs(r["z"]) >= MATERIAL_Z])}.values()))
    for r in _monitor_pool:
        r["slot_priority"] = slot_priority(r)
    _rest = sorted(
        [r for r in _monitor_pool if r["ltcode"] not in _dive_codes],
        key=lambda r: (-r["slot_priority"], -abs(r.get("z") or 0), r["ltcode"] or ""))
    _dsorted = sorted(payload["deep_dive"],
                      key=lambda r: (-slot_priority(r), -abs(r.get("z") or 0),
                                     r["ltcode"] or ""))
    monitor_src = _dsorted + _rest
    def _mkey(r):
        if re.search(r"\bmargin\b", str(r.get("display") or r.get("name") or ""), re.I):
            return "margin"
        return _family(r["name"], r)
    _seen_m, _msrc = set(), []
    for r in monitor_src:
        k = _mkey(r)
        if k in _seen_m:
            continue
        _seen_m.add(k)
        _msrc.append(r)
    monitor_src = _msrc
    monitor = []
    for r in monitor_src[:MONITOR_N]:
        fb = r.get("forward_bar") or {}
        fact = (f"{_fmt_growth(r['latest_growth'], r['unit'])} y/y, pace "
                f"{_fmt_accel(r['latest_accel'], r['unit'])}")
        if fb.get("level_to_hold_growth") is not None:
            _bar = format_row_level(
                fb.get("level_to_hold_display"), r.get("unit_label"),
                r.get("unit_decimals", 2),
                r.get("unit_status") == "conflicted" or r.get("flow_rate_mode"),
                r.get("currency"))
            _lead = ("" if _bar == "level withheld" else f"{_bar} ")
            proof = (f"{_lead}holds the y/y rate against {fb.get('next_base_period')}; "
                     f"flat q/q gives "
                     f"{_fmt_growth(fb.get('growth_if_level_flat'), r['unit'])}")
        else:
            proof = "next print against the year-ago quarter"
        _g = next((x for x in ((payload.get("guidance_check") or {}).get("scored") or [])
                   if x.get("ltcode") == r["ltcode"]
                   and x.get("guide_display_text")), None)
        guided = None
        if _g:
            guided = _g["guide_display_text"]
            _imp = _g.get("guide_implied_growth")
            proof = (f"guided {guided}"
                     + (f", implying {_fmt_growth(_imp, r['unit'])}"
                        if _imp is not None else "")
                     + "; the " + str(fb.get("next_base_period") or "year-ago")
                     + " base alone gives "
                     + _fmt_growth(fb.get("growth_if_level_flat"), r["unit"])
                     + " if flat q/q"
                     if fb.get("level_to_hold_growth") is not None
                     else f"guided {guided}")
        _up = (r.get("latest_accel") or 0) > 0
        _bps = r.get("unit") == "bps"
        _lower = bool(r.get("polarity") and r["polarity"] < 0)
        _comp = r.get("comp_flag")
        _next_comp = None
        _nb = fb.get("next_base_period")
        _ps, _gs = r.get("period_series") or [], r.get("growth_series") or []
        if _nb and _nb in _ps and len(_ps) == len(_gs):
            _gb = _gs[_ps.index(_nb)]
            _hist = sorted(x for x in _gs if x is not None)
            if _gb is not None and len(_hist) >= 6:
                _q1 = _hist[len(_hist) // 4]
                _q3 = _hist[(3 * len(_hist)) // 4]
                _next_comp = "hard" if _gb >= _q3 else ("easy" if _gb <= _q1 else None)
                _cb = _gs[-5] if len(_gs) >= 5 else None
                if _cb is not None and ((_next_comp == "easy" and _gb > _cb)
                                        or (_next_comp == "hard" and _gb < _cb)):
                    _next_comp = None
        _streak = r.get("streak") or 0
        _lvl_g = r.get("latest_growth") or 0
        _noun = ratio_noun(r.get("name"))
        if guided:
            question = "Does it land inside the guided range?"
        elif _bps and _noun == "margin":
            if _lower:
                question = ("Does the ratio keep rising, or does it turn back?" if _up
                            else ("Does the improvement hold?" if _lvl_g < 0
                                  else "Does the ratio stop rising?"))
            elif _up and _lvl_g < 0:
                question = "Does the compression keep easing, or does the margin turn back down?"
            elif _up:
                question = "Does the expansion continue, or was this the widest point?"
            elif _lvl_g > 0:
                question = "Does the margin keep widening, or does the moderation continue?"
            else:
                question = "Does the compression stop, or does another quarter come off?"
        elif _bps:
            _n = _noun or ("ratio" if re.search(r"ratio", str(r.get("name") or ""), re.I)
                           else str(r.get("display") or r.get("name") or "ratio"))
            if _lower:
                question = ("Does the %s keep rising, or does it turn back?" % _n if _up
                            else ("Does the lower %s hold?" % _n if _lvl_g < 0
                                  else "Does the %s stop rising?" % _n))
            elif _up:
                question = "Does %s keep rising at this pace?" % _n
            elif _lvl_g > 0:
                question = "Does %s keep rising, or does the gain keep shrinking?" % _n
            else:
                question = "Does the fall in %s stop, or does another quarter come off?" % _n
        elif _next_comp in ("hard", "easy"):
            question = ("Does the pace hold against a harder comparison?"
                        if _next_comp == "hard" else
                        "Does growth hold up against an easier comparison?")
        elif _streak >= 3:
            question = ("Does the %d-quarter run of acceleration extend?" % _streak
                        if _up else
                        "Does the %d-quarter run of deceleration extend?" % _streak)
        elif (r.get("family") or "") in ("fee_revenue", "fee_income"):
            question = ("Does fee growth keep outrunning the asset base?" if _up
                        else "Does fee growth stabilise?")
        elif (r.get("family") or "") == "revenue":
            question = ("Does the top line hold this pace on a bigger base?" if _up
                        else "Does the top line stop decelerating?")
        elif (r.get("family") or "") == "cash":
            question = ("Does cash conversion keep improving?" if _up
                        else "Does cash conversion stop deteriorating?")
        elif (r.get("family") or "") in ("volumes", "unit_revenue", "unit_cost"):
            question = ("Does the unit trend extend?" if _up
                        else "Does the unit trend turn?")
        else:
            question = ("Does the pickup hold on a bigger base?" if _up
                        else "Does the slowdown continue?")
        if r.get("kind") == "pre_growth" and not guided:
            _vs = [v for v in (r.get("value_series") or [])]
            _ps = r.get("period_series") or []
            if len(_vs) >= 4 and _vs[-1] is not None:
                _cur = _vs[-1]
                _dp = 0 if all(float(x).is_integer() for x in _vs if x is not None) else 1
                _F = lambda v: ("%." + str(_dp) + "f%%") % v
                _up_r = (r.get("latest_accel") or 0) > 0
                fact = "%s, %s vs last quarter" % (_F(_cur), _fmt_accel(r.get("latest_accel"), "pct"))
                question = ("Does the pickup continue next quarter?" if _up_r
                            else "Does the rate stop falling?")
                _nb = _vs[-4]
                proof = (("above %s extends the pickup" if _up_r else "below %s extends the decline")
                         % _F(_cur))
                if _nb is not None and len(_ps) >= 4:
                    proof += "; %s read %s" % (_ps[-4], _F(_nb))
        _ae = r.get("adjustment_effect") or {}
        if _ae and (_ae.get("share_of_move") or 0) >= 0.3:
            question = ("How much of the move holds once the %s (%s) drops out?"
                        % (_ae.get("label"), _ae.get("kind")))
        if _ae.get("ex_level_display") and not _ae.get("carried_from"):
            proof = (proof + "; " if proof else "") + (
                "excluding the item%s the quarter read about %s (InSync arithmetic on "
                "management's figure)" % ("s" if _ae.get("ex_both") else "", _ae["ex_level_display"]))
        monitor.append({"variable": r.get("display") or r["name"], "ltcode": r["ltcode"],
                        "current_fact": fact, "next_proof": proof,
                        "question": question,
                        "proof_source": ("company guidance" if guided
                                         else "arithmetic against the year-ago base"),
                        "guided_next_quarter": guided,
                        "guide_implied_growth": (_g or {}).get("guide_implied_growth"),
                        "level_to_hold": fb.get("level_to_hold_growth"),
                        "growth_if_flat": fb.get("growth_if_level_flat"),
                        "next_base_period": fb.get("next_base_period"),
                        "unit": r["unit"], "implication": r.get("implication")})
    payload["monitor"] = monitor

    _ss = payload.get("scan_status") or {}
    if _ss.get("unresolved_units"):
        _unres = set(_ss.get("unresolved_unit_lines") or [])
        _in = [x for x in (payload.get("implication_ledger") or [])
               if x.get("metric") in _unres]
        _ss["unresolved_units_in_counts"] = [
            {"metric": x.get("metric"), "population": x.get("population"),
             "implication": x.get("implication"),
             "displayed": bool(x.get("displayed_in_ranked_table"))}
            for x in _in]

    _seen, _q = set(), []
    for _sec in ("deep_dive", "accelerating", "decelerating", "primary_context"):
        for _r in (payload.get(_sec) or []):
            _nm = _r.get("display") or _r.get("name")
            if ("quantised_source" in (_r.get("flags") or [])
                    and _nm and _nm not in _seen):
                _seen.add(_nm)
                _q.append({"metric": _nm, "step_bps": _r.get("quant_step_bps")})
    _ss["quantised_lines"] = _q

    _pairs = {}
    for kind, blob in (("diverging", payload.get("divergences") or []),
                       ("confirmed", payload.get("confirmations") or [])):
        for d in blob:
            for m in (d.get("metrics") or []):
                if not m.get("ltcode"):
                    continue
                e = _pairs.setdefault(m["ltcode"], {"status": set(), "pairs": []})
                e["status"].add(kind)
                other = [x.get("display") for x in (d.get("metrics") or [])
                         if x.get("ltcode") != m["ltcode"]]
                e["pairs"].append({"kind": kind, "rule": d.get("rule"),
                                   "against": other[0] if other else None,
                                   "basis": d.get("basis", "observed relationship")})
    for key in ("headline_moves", "accelerating", "decelerating", "primary_context",
                "deep_dive", "monitor", "heatmap"):
        for r in (payload.get(key) or []):
            a = r.get("assessment")
            if not a:
                continue
            e = _pairs.get(r.get("ltcode"))
            if not e:
                a["cross_metric"] = {"status": "not corroborated", "pairs": []}
            else:
                st = e["status"]
                a["cross_metric"] = {
                    "status": ("diverging and confirmed" if len(st) > 1
                               else next(iter(st))),
                    "pairs": e["pairs"]}

    def _story_weight(d):
        return sum(abs(m.get("z") or 0.0) for m in (d.get("metrics") or []))

    _stories = ([dict(d, kind="divergence") for d in (payload.get("divergences") or [])]
                + [dict(d, kind="confirmation")
                   for d in (payload.get("confirmations") or [])])
    for _d in _stories:
        for _m in (_d.get("metrics") or []):
            _sx = series_by_code.get(_m.get("ltcode"))
            if _sx is None:
                continue
            _m.setdefault("growth", _sx.latest_growth)
            _m.setdefault("prev_growth", _sx.prev_growth)
            _m.setdefault("prev_period", _sx.periods[-2] if len(_sx.periods) >= 2 else None)
            _m.setdefault("unit", _sx.unit)
            _m.setdefault("kind", _sx.kind)
    def _calc_leg(d):
        return any(getattr(series_by_code.get(m.get("ltcode")), "insync_calculated", False)
                   for m in (d.get("metrics") or []))
    _stories.sort(key=lambda d: (0 if d["kind"] == "divergence" else 1, _calc_leg(d),
                                 -_story_weight(d), d.get("rule") or ""))
    for _d in _stories:
        for _m in (_d.get("metrics") or []):
            _sx = series_by_code.get(_m.get("ltcode"))
            if _sx is None or not getattr(_sx, "insync_calculated", False) \
                    or not re.search(r"ebitda", _sx.name or "", re.I):
                continue
            _br = _ebitda_bridge(_sx, series_by_code)
            if _br:
                _m["bridge"] = _br
    payload["primary_story"] = (_stories[0] if _stories else None)
    payload["supporting_stories"] = _stories[1:3]
    _aq, _seen_aq = [], set()
    for _bk in ("headline_moves", "accelerating", "decelerating", "deep_dive", "rate_kpis",
                "primary_context", "quarter_strip"):
        _src = payload.get(_bk) or []
        if _bk == "quarter_strip":
            _src = (_src or {}).get("cells") or []
        for _r in _src:
            _e = _r.get("adjustment_effect")
            if _e and _r.get("ltcode") not in _seen_aq:
                _seen_aq.add(_r.get("ltcode"))
                _aq.append(dict(_e, ltcode=_r.get("ltcode"), display=_r.get("display") or _r.get("name"),
                                move_display=_fmt_growth(_r.get("latest_growth"), _r.get("unit") or "pct")
                                if _r.get("latest_growth") is not None else None))
    payload["adjustment_quality"] = _aq
    payload["commentary_supplied"] = bool(getattr(args, "commentary", None)
                                          and _load_commentary(getattr(args, "commentary", None)))
    payload["story_basis"] = (
        "no cross-metric relationship cleared the bars; the findings are "
        "single-line" if not _stories else
        "the strongest relationship among the lines that cleared the bars, "
        "divergences ranked ahead of confirmations")

    _pri = payload.get("primary_story")
    monitor = payload.get("monitor") or []
    if _pri and (_pri.get("metrics") or []):
        _legs = [m for m in _pri["metrics"] if m.get("display")][:2]
        if len(_legs) == 2:
            _apart = _pri.get("kind") == "divergence"
            if len(monitor) >= MONITOR_N:
                monitor.pop()
            monitor.append({
                "variable": _legs[0]["display"] + " versus " + _legs[1]["display"],
                "ltcode": None,
                "current_fact": "; ".join(
                    (_fmt_growth(m.get("growth"), m.get("unit") or "pct") + " y/y"
                     if m.get("growth") is not None else
                     "pace " + _fmt_accel(m.get("accel"), m.get("unit") or "pct"))
                    for m in _legs),
                "question": (_pair_question(_legs) if _apart else _together_question(_legs, _pri)),
                "next_proof": (_pair_proof(_legs, series_by_code) or
                               ("both lines against "
                                + str(next((( r.get("forward_bar") or {}).get(
                                    "next_base_period") for r in payload["deep_dive"]
                                    if (r.get("forward_bar") or {}).get(
                                        "next_base_period")), "year-ago"))
                                + "; the gap between them is the test")),
                "proof_source": "arithmetic against the year-ago base",
                "unit": _legs[0].get("unit") or "pct",
                "from_relationship": _pri.get("rule"),
                "implication": None})

    payload["sector_tables"] = {
        "labels": dict(sorted(TEMPLATE_LABEL.items())),
        "rule_owners": {k: (list(v) if not isinstance(v, str) else [v])
                        for k, v in sorted(RULE_OWNER.items())},
        "rule_universal": sorted(RULE_UNIVERSAL),
        "has_routing_evidence": sorted(TEMPLATE_EVIDENCE),
        "has_ranking_rules": sorted(TEMPLATE_PRIORITY),
        "declared_label_only": dict(sorted(TEMPLATE_LABEL_ONLY.items())),
        "validated_by_live_fixture": dict(sorted(TEMPLATE_VALIDATED.items())),
        "table_semantics": {
            "validated_by_live_fixture": "a live note has been routed to this "
                                         "template and passed every gate; it says "
                                         "nothing about how complete the template is",
            "declared_label_only": "the template names the sector and supplies no "
                                   "ranking rules, so a note routed here earns no "
                                   "sector uplift and is ordered on the general rules",
            "both": "a template in both lists has been exercised end to end and is "
                    "still incomplete - the run proved the routing, not the ranking",
        },
    }

    payload["provenance"] = _provenance(payload, series_by_code)
    payload["source_records"] = _source_records(raw, payload)
    payload["not_retrieved"] = _not_retrieved(raw, series)
    payload["ranking_disclosure"] = _ranking_disclosure(payload)
    payload["placeholders"] = _placeholders(payload)

    _ss = payload.get("scan_status") or {}
    _pct = _ss.get("primary_supporting_pct")
    if _pct is None and not getattr(args, "allow_partial", False):
        _fail("build", "coverage cannot be computed: raw.json records no primary or "
                       "supporting lines in its discovery plan (planned_codes). Re-run "
                       "ingest with --discovered and --sweep; --allow-partial builds it "
                       "anyway as a labelled partial read")
    if (_pct is not None and _pct < PRIMARY_SUPPORTING_FLOOR
            and not getattr(args, "allow_partial", False)):
        raise SystemExit(
            "REFUSED: primary and supporting coverage is %.0f%% (%s of %s lines), "
            "below the %.0f%% floor.\n"
            "A note this thin cannot carry a statement about the company - its "
            "direction, its executive summary and every count would be scoped to "
            "a fraction of the disclosed surface.\n"
            "Re-run step 3: check that the keyword sweep ran, that every batch "
            "the plan printed was fetched, and that scratch/responses held only "
            "this run's files. `compute.py verify` names the codes that are "
            "missing.\n"
            "To publish it anyway as a deliberately partial read, pass "
            "--allow-partial; the page then carries the partial-scan label."
            % (100 * _pct, _ss.get("primary_supporting_retrieved"),
               _ss.get("primary_supporting_planned"),
               100 * PRIMARY_SUPPORTING_FLOOR))

    if quarters < MIN_Q_FOR_RANK:
        payload["guard"] = (
            f"Only {quarters} quarters of reported history are available, below the "
            f"{MIN_Q_FOR_RANK}-quarter minimum. Acceleration cannot be scored against a "
            "series' own history at this length; the note reports levels and growth only.")

    def _rv(r):
        vs = r.get("value_series") or []
        ps = r.get("period_series") or []
        if r.get("kind") != "pre_growth" or not vs or vs[-1] is None:
            return
        cur = vs[-1]
        prev = vs[-2] if len(vs) >= 2 else None
        ya = vs[-5] if len(vs) >= 5 else None
        r["rate_decimals"] = 0 if all(float(v).is_integer() for v in vs if v is not None) else 1
        r["rate_view"] = {
            "latest": cur, "prev": prev, "prev_period": ps[-2] if len(ps) >= 2 else None,
            "year_ago": ya, "year_ago_period": ps[-5] if len(ps) >= 5 else None,
            "vs_last_q": (round(cur - prev, 6) if prev is not None else None),
            "vs_year_ago": (round(cur - ya, 6) if ya is not None else None)}
    for _k in ("rate_kpis", "deep_dive", "headline_moves", "accelerating", "decelerating",
               "primary_context", "quiet_headline"):
        for _r in payload.get(_k) or []:
            if isinstance(_r, dict):
                _rv(_r)
    _shown = set(_displayed_ltcodes(payload)[0])
    _unres = []
    for x in sorted(series, key=lambda y: y.ltcode or ""):
        _ = x.polarity
        if getattr(x, "polarity_state", None) == "undeclared":
            _unres.append({"ltcode": x.ltcode, "name": x.name, "displayed": x.ltcode in _shown})
    payload["unresolved_polarity"] = _unres
    payload["inferred_polarity"] = [
        {"ltcode": x.ltcode, "name": x.name, "displayed": x.ltcode in _shown}
        for x in sorted(series, key=lambda y: y.ltcode or "")
        if getattr(x, "polarity_state", None) == "inferred"]
    payload["polarity_registry_codes"] = len(POLARITY_REGISTRY)
    payload["exclusion_categories"] = _exclusion_categories(payload.get("excluded") or [])
    payload["warnings"] = _build_warnings(payload, series) + [
        {"code": "polarity_undeclared", "ltcode": u["ltcode"],
         "message": "%s (%s): no polarity rule declares which way this line is better; "
                    "it reads Ambiguous" % (u["ltcode"], u["name"])}
        for u in _unres if u["displayed"]] + [
        {"code": "polarity_inferred", "ltcode": u["ltcode"],
         "message": "%s (%s): read as revenue because the name carries no cost word; "
                    "no rule declares its direction" % (u["ltcode"], u["name"])}
        for u in payload["inferred_polarity"] if u["displayed"]] + list(payload.get("warnings") or [])
    os.makedirs(args.out_dir, exist_ok=True)
    _pb = (getattr(args, "prepared_by", None) or "").strip()
    if _pb:
        payload["prepared_by"] = _pb
    out = os.path.join(args.out_dir,
                       f"{ticker_slug(payload['ticker'])}_inflection_computed.json")
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)

    summary = {
        "written": out,
        "ticker": payload["ticker"],
        "latest_quarter": payload["latest_quarter"],
        "coverage": payload["coverage"],
        "top_accelerating": [(r["name"], round(r["z_display"], 2)) for r in acc_rows[:5]],
        "top_decelerating": [(r["name"], round(r["z_display"], 2)) for r in dec_rows[:5]],
        "divergences": [d["rule"] for d in payload["divergences"]],
        "guard": payload.get("guard"),
        "warnings": [w["message"] for w in payload.get("warnings") or []][:12],
    }
    print(json.dumps(summary, indent=2))


EXCLUSION_CATEGORY = [
    ("duplicate of another line", "duplicates of another line",
     {"duplicate", "derived_twin", "reported_twin", "adjusted_twin_thin", "shadow_twin",
      "shadow_no_adjustments", "shadow_company_adjusted", "shadow_component", "shadow_basic",
      "shadow_company_reported"}),
    ("with too little history to score", None, {"short_history", "short_accel_history"}),
    ("with a comparison quarter missing from the feed", None,
     {"year_ago_missing", "prior_quarter_missing", "prior_year_ago_missing"}),
    ("with a value returned in a form that could not be read", None,
     {"unparsed", "year_ago_unreadable", "prior_quarter_unreadable",
      "prior_year_ago_unreadable", "latest_unreadable"}),
    ("with no reading in the latest quarter", None, {"stale"}),
    ("not a continuous quarterly series", None, {"step_like", "cumulative", "spliced"}),
    ("with an unstable year-ago comparison", None,
     {"base_unstable", "volatile_base", "zero_read_as_missing"}),
    ("modelled rather than reported", None, {"modelled", "modelled_input"}),
    ("a count quoted in money by the source", "counts quoted in money by the source",
     {"count_as_money"}),
]


def _exclusion_categories(excluded):
    out, left = [], list(excluded)
    for one, many, codes in EXCLUSION_CATEGORY:
        n = sum(1 for e in left if e.get("reason_code") in codes)
        if n:
            out.append({"category": one if n == 1 or not many else many, "count": n})
        left = [e for e in left if e.get("reason_code") not in codes]
    if left:
        out.append({"category": "held out for another data-quality reason", "count": len(left)})
    return out


WARN_REASON_CODES = {
    "year_ago_unreadable": "year-ago value unreadable",
    "prior_quarter_unreadable": "last quarter's value unreadable",
    "prior_year_ago_unreadable": "last quarter's year-ago value unreadable",
    "latest_unreadable": "latest value unreadable",
    "year_ago_missing": "year-ago quarter missing from feed",
    "prior_quarter_missing": "last quarter missing from feed",
    "prior_year_ago_missing": "last quarter's year-ago quarter missing from feed",
    "unclassified": "held out by a stage that recorded no reason",
    "vendor_ratio_mismatch": "the source's ratio disagrees with its own parts; held out",
}


def _build_warnings(payload, series):
    out = []
    _cm = [e for e in payload.get("excluded") or [] if e.get("reason_code") == "count_as_money"]
    if _cm:
        out.append({"code": "count_as_money", "ltcode": ",".join(e["ltcode"] for e in _cm),
                    "message": "%d line(s) name a count but are quoted in money by the source "
                               "and are held out as a unit conflict: %s"
                               % (len(_cm), ", ".join("%s (%s)" % (e["ltcode"], e.get("name"))
                                                      for e in _cm))})
    for e in payload.get("excluded") or []:
        why = WARN_REASON_CODES.get(e.get("reason_code"))
        if why:
            out.append({"code": e["reason_code"], "ltcode": e["ltcode"],
                        "message": "%s (%s): %s" % (e["ltcode"], e.get("name"), why)})
    for x in series:
        g = getattr(x, "grid", None) or {}
        if g.get("misaligned") or g.get("label_disagrees"):
            out.append({"code": "grid_misaligned", "ltcode": x.ltcode,
                        "message": "%s (%s): quarter-end dates do not sit on a quarterly "
                                   "grid or disagree with the fiscal labels (%s)"
                                   % (x.ltcode, x.name, ", ".join(
                                       [str(d) for d in g.get("misaligned") or []]
                                       + [str(d.get("date")) for d in g.get("label_disagrees") or []])[:120])})
    return out


def cmd_dives(args):
    import contextlib
    import io
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        args.out_dir = td
        args.prepared_by = None
        args.commentary = None
        with contextlib.redirect_stdout(io.StringIO()):
            cmd_build(args)
        f = next(x for x in os.listdir(td) if x.endswith("_inflection_computed.json"))
        with open(os.path.join(td, f), encoding="utf-8") as fh:
            p = json.load(fh)
    cash = [{"ltcode": r.get("ltcode"), "display": r.get("display") or r.get("label")}
            for r in ((p.get("cash_conversion") or {}).get("rows") or [])
            if r.get("ltcode") in ("CF_P_108", "CF_P_110")]
    print(json.dumps({"ticker": p.get("ticker"), "latest_quarter": p.get("latest_quarter"),
                      "deep_dives": [{"ltcode": r.get("ltcode"),
                                      "display": r.get("display") or r.get("name"),
                                      "attributed_to_metric": r.get("display") or r.get("name")}
                                     for r in p.get("deep_dive") or []],
                      "cash_panel_quoted_rows": cash}, indent=2))


def _rows_from_envelope(blob, out):
    if isinstance(blob, list):
        for x in blob:
            _rows_from_envelope(x, out)
        return
    if not isinstance(blob, dict):
        return
    code = blob.get("LTCODE") or blob.get("ltcode")
    qed = blob.get("QUARTERENDDATE") or blob.get("quarter_end_date")
    if code and qed and ("VALUE" in blob or "value" in blob):
        out.append(blob)
        return
    for v in blob.values():
        _rows_from_envelope(v, out)


_MISSING_NOTE = re.compile(r"MISSING DATA[^:]*:\s*LTcode\(s\)\s*([^.]*?)\s+returned",
                           re.I)


def _absent_upstream(blob, out):
    if isinstance(blob, list):
        for x in blob:
            _absent_upstream(x, out)
        return
    if not isinstance(blob, dict):
        return
    for note in (blob.get("notes") or []):
        for m in _MISSING_NOTE.finditer(str(note)):
            for c in re.split(r"[,\s]+", m.group(1)):
                c = c.strip()
                if re.fullmatch(r"(?:IS|BS|CF|KM|SD|RT)_[PS]_[A-Za-z0-9.\-]+", c or ""):
                    out.add(c)
    for v in blob.values():
        if isinstance(v, (dict, list)):
            _absent_upstream(v, out)


def _guidance_arrays(blob):
    g, o = [], []

    def walk(b):
        if isinstance(b, list):
            for x in b:
                walk(x)
            return
        if not isinstance(b, dict):
            return
        for key, target in (("guidance", g), ("outlook", o)):
            v = b.get(key)
            if isinstance(v, list):
                target += [x for x in v if isinstance(x, dict)]
        for k, v in b.items():
            if k not in ("guidance", "outlook"):
                walk(v)

    walk(blob)
    return g, o


RUN_DIR_PAT = re.compile(r"^(?P<t>[A-Za-z0-9.\-]+)_(?P<q>[1-4]Q\d{2})_(?P<id>[A-Za-z0-9\-]+)$")
EXACT_MATCH_TYPES = ("exact_ticker", "exact_company")
CONVERSION_NOTE_PAT = re.compile(
    r"\bconver(?:t|ted|sion)\b[^.]*\b(currency|usd|eur|gbp|fx|exchange rate)|"
    r"\b(currency|fx)\b[^.]*\bconver(?:t|ted|sion)\b", re.I)


class IngestError(Exception):
    pass


def _fail(stage, message, code=1):
    print(json.dumps({"status": "error", "stage": stage, "message": message}, indent=2))
    sys.stderr.write("%s: %s\n" % (stage, message))
    sys.exit(code)


def _expand_inputs(paths):
    files = []
    for path in paths:
        if os.path.isdir(path):
            files += [os.path.join(path, f) for f in sorted(os.listdir(path))
                      if f.endswith(".json")]
        elif os.path.isfile(path):
            files.append(path)
        else:
            raise IngestError("input %r does not exist" % path)
    return files


def load_responses(paths):
    out = []
    for f in _expand_inputs(paths if isinstance(paths, (list, tuple)) else [paths]):
        try:
            with open(f, encoding="utf-8") as fh:
                out.append((f, json.load(fh)))
        except (json.JSONDecodeError, OSError) as e:
            raise IngestError("could not read %s: %s" % (f, e))
    return out


def _walk_dicts(blob):
    if isinstance(blob, dict):
        yield blob
        for v in blob.values():
            yield from _walk_dicts(v)
    elif isinstance(blob, list):
        for v in blob:
            yield from _walk_dicts(v)


def _truncated_pages(blob):
    hits = []
    for d in _walk_dicts(blob):
        meta = d.get("meta") if isinstance(d.get("meta"), dict) else None
        for m in ([meta] if meta else []) + ([d] if "has_more" in d else []):
            try:
                rem = int(m.get("remaining_items") or 0)
            except (TypeError, ValueError):
                rem = 0
            if m.get("has_more") is True or str(m.get("has_more")).lower() == "true" or rem > 0:
                hits.append({"has_more": m.get("has_more"), "remaining_items": m.get("remaining_items"),
                             "offset": m.get("offset"), "limit": m.get("limit")})
                break
    return hits


def _conversion_notes(blob):
    found = []
    for d in _walk_dicts(blob):
        for note in (d.get("notes") or []) if isinstance(d.get("notes"), list) else []:
            if CONVERSION_NOTE_PAT.search(str(note)):
                found.append(str(note)[:160])
    return found


def _foreign_ticker_rows(blob, ticker_id):
    bad = set()
    for d in _walk_dicts(blob):
        if "TICKER_ID" in d or "ticker_id" in d:
            tid = d.get("TICKER_ID", d.get("ticker_id"))
            if (d.get("LTCODE") or d.get("ltcode") or d.get("GUIDE_TEXT") or d.get("CALL_DATE")) \
                    and str(tid).strip() != str(ticker_id).strip():
                bad.add(str(tid))
    return sorted(bad)


def _run_folder(path):
    d = os.path.abspath(path if os.path.isdir(path) else os.path.dirname(path))
    while True:
        if RUN_DIR_PAT.match(os.path.basename(d)):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            return None
        d = parent


def _pid_alive(pid):
    try:
        pid = int(pid)
    except (ValueError, TypeError):
        return False
    if os.name == "nt":
        import ctypes
        k32 = ctypes.windll.kernel32
        h = k32.OpenProcess(0x1000, False, pid)
        if not h:
            return k32.GetLastError() == 5
        try:
            code = ctypes.c_ulong()
            ok = k32.GetExitCodeProcess(h, ctypes.byref(code))
            return bool(ok) and code.value == 259
        finally:
            k32.CloseHandle(h)
    try:
        os.kill(pid, 0)
        return True
    except PermissionError:
        return True
    except OSError:
        return False


def _take_lock(run_dir, ticker):
    lock = os.path.join(run_dir, ".ingest.lock")
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        try:
            with open(lock, encoding="utf-8") as fh:
                held = json.load(fh)
        except (OSError, ValueError):
            held = {}
        if _pid_alive(held.get("pid")):
            raise IngestError("run folder %s is locked by another ingest (pid %s, ticker %s); "
                              "use a fresh run folder for each run"
                              % (run_dir, held.get("pid"), held.get("ticker")))
        os.remove(lock)
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump({"pid": os.getpid(), "ticker": ticker}, fh)
    return lock


def _row_key(r):
    return tuple(str(r.get(k) if r.get(k) is not None else r.get(k.lower()) or "")
                 for k in ("TICKER_ID", "LTCODE", "FISCALPERIOD", "QUARTERENDDATE", "VALUE",
                           "SOURCE_DERIVED", "DATA_SOURCE"))


_EXACT_MATCH = ("exact_ticker", "exact_company")


def _norm_company(x):
    x = re.sub(r"[^a-z0-9 ]+", " ", str(x or "").lower())
    x = re.sub(r"\b(inc|incorporated|corp|corporation|co|company|ltd|limited|plc|ag|sa|nv|"
               r"the|holdings?|group)\b", " ", x)
    return " ".join(x.split())


def _read_resolver(files, args):
    if not files:
        return None
    rows = []
    for f, blob in load_responses(list(files)):
        for d in _walk_dicts(blob):
            if d.get("match_type") is not None and d.get("ticker_id") is not None:
                rows.append(d)
    if not rows:
        raise IngestError("the saved search_tickerid response holds no resolver rows; file the "
                          "search_tickerid result as returned")
    tid = str(args.ticker_id)
    mine = [r for r in rows if str(r.get("ticker_id")) == tid]
    if not mine:
        raise IngestError("the resolver response holds no row for ticker_id %s (it returned %s); "
                          "this run's ticker_id did not come from it"
                          % (tid, ", ".join(sorted({str(r.get("ticker_id")) for r in rows}))))
    exact = [r for r in mine if str(r.get("match_type")) in _EXACT_MATCH]
    if not exact:
        raise IngestError("ticker_id %s was resolved only by a %s match, not an exact one; "
                          "confirm the company with the user before scanning"
                          % (tid, "/".join(sorted({str(r.get("match_type")) for r in mine}))))
    exact.sort(key=lambda r: _EXACT_MATCH.index(str(r.get("match_type"))))
    best = exact[0]
    tickers = {str(r.get("ticker") or "").upper() for r in exact}
    if args.ticker.upper() not in tickers:
        raise IngestError("the run is for %s but the resolver's exact row for ticker_id %s is %s"
                          % (args.ticker.upper(), tid, "/".join(sorted(tickers))))
    name = best.get("company_name")
    said = getattr(args, "company", None)
    if said and _norm_company(said) not in ("", _norm_company(args.ticker)) \
            and _norm_company(said) != _norm_company(name):
        raise IngestError("the run names the company %r but the resolver returned %r for "
                          "ticker_id %s; correct run.json" % (said, name, tid))
    want = getattr(args, "match_type", None)
    if want and want not in [str(r.get("match_type")) for r in mine]:
        raise IngestError("the run declares match_type %s but the resolver returned %s for "
                          "ticker_id %s" % (want, "/".join(sorted({str(r.get("match_type"))
                                                                   for r in mine})), tid))
    return {"match_type": str(best.get("match_type")), "ticker_id": tid,
            "source": "resolver response", "company_name": name,
            "resolver_latest_fiscal_quarter": best.get("latest_fiscal_quarter"),
            "resolver_latest_quarter_end_date": best.get("latest_quarter_end_date"),
            "insync_subindustry": best.get("insync_subindustry")}


def cmd_ingest(args):
    try:
        return _cmd_ingest(args)
    except IngestError as e:
        print(json.dumps({"status": "error", "stage": "ingest", "message": str(e)}, indent=2))
        sys.exit(1)


def _cmd_ingest(args):
    run_dir = _run_folder(args.out)
    want = "%s_%s_<runid>" % (ticker_slug(args.ticker).upper(), args.latest_quarter.upper())
    if not run_dir:
        raise IngestError("--out %s is not inside a run folder; write each run to "
                          "scratch/%s/ (one folder per run, so two runs cannot share "
                          "files)" % (args.out, want))
    m_rd = RUN_DIR_PAT.match(os.path.basename(run_dir))
    if (m_rd.group("t").upper() != ticker_slug(args.ticker).upper()
            or m_rd.group("q").upper() != args.latest_quarter.upper()):
        raise IngestError("run folder %s does not belong to %s %s (want %s)"
                          % (os.path.basename(run_dir), args.ticker, args.latest_quarter, want))
    for pth in args.files:
        if _run_folder(pth) != run_dir:
            raise IngestError("response input %s is outside this run's folder %s; a file "
                              "from another run would be merged silently" % (pth, run_dir))
    lock = _take_lock(run_dir, args.ticker)
    try:
        return _cmd_ingest_locked(args, run_dir)
    finally:
        try:
            os.remove(lock)
        except OSError:
            pass


def _cmd_ingest_locked(args, run_dir):
    rows = []
    loaded = load_responses(args.files)
    files = [f for f, _ in loaded]
    absent = set()
    _empty_ok = 0
    trunc, foreign, convert = [], {}, []
    for f, blob in loaded:
        if _truncated_pages(blob):
            trunc.append(os.path.basename(f))
        bad = _foreign_ticker_rows(blob, args.ticker_id)
        if bad:
            foreign[os.path.basename(f)] = bad
        convert += [(os.path.basename(f), n) for n in _conversion_notes(blob)]
        _rows_from_envelope(blob, rows)
        _absent_upstream(blob, absent)
        _empty_ok += _count_empty_success(blob)
    _side = []
    _goff = {int(((b.get("meta") or {}) if isinstance(b, dict) else {}).get("offset") or 0)
             for s in (args.guidance or []) for _f, b in load_responses([s])}
    for src in list(args.guidance or []) + list(getattr(args, "annual", None) or []) \
            + list(getattr(args, "call_dates", None) or []):
        for f, blob in load_responses([src]):
            _side.append((f, blob))
            _gm = ((blob.get("meta") or {}) if isinstance(blob, dict) else {})
            if _truncated_pages(blob) and not (src in (args.guidance or []) and int(
                    _gm.get("offset") or 0) + int(_gm.get("limit") or 100) in _goff):
                trunc.append(os.path.basename(f))
            bad = _foreign_ticker_rows(blob, args.ticker_id)
            if bad:
                foreign[os.path.basename(f)] = bad
            convert += [(os.path.basename(f), n) for n in _conversion_notes(blob)]
    _seen, _uniq = set(), []
    for _r in rows:
        _k = _row_key(_r)
        if _k not in _seen:
            _seen.add(_k)
            _uniq.append(_r)
    _dupes = len(rows) - len(_uniq)
    rows = _uniq
    if trunc:
        raise IngestError("has_more is true (or remaining_items > 0) in %s: a page was "
                          "truncated. Halve that batch, or page guidance on offset, and "
                          "re-fetch before ingesting" % ", ".join(sorted(set(trunc))))
    if foreign:
        raise IngestError("rows from another ticker_id (this run is %s): %s. Remove those "
                          "files; they are left over from another run"
                          % (args.ticker_id, "; ".join("%s carries %s" % (k, ",".join(v))
                                                       for k, v in sorted(foreign.items()))))
    if convert:
        raise IngestError("the vendor reports a currency conversion (%s: %r). The note "
                          "reports figures in the company's own currency; stop and ask "
                          "the user how to proceed" % convert[0])
    _resolved = _read_resolver(getattr(args, "resolve", None), args)
    _empty_batches = []
    if _empty_ok and args.discovered:
        try:
            _got = {(r.get("LTCODE") or r.get("ltcode")) for r in rows}
            _empty_batches = [b for b in _planned_batches(args.discovered, getattr(args, "sweep", None),
                                                          args.latest_quarter)
                              if b and not (set(b) & _got)]
            if _empty_batches and _empty_ok >= len(_empty_batches):
                for b in _empty_batches:
                    absent.update(b)
            else:
                _empty_batches = []
        except Exception as e:
            sys.stderr.write(f"empty-batch check skipped: {e}\n")
            _empty_batches = []

    lineitems, seen = [], set()
    for r in rows:
        code = r.get("LTCODE") or r.get("ltcode")
        if code in seen:
            continue
        seen.add(code)
        lineitems.append({"ltcode": code,
                          "lineitem": r.get("LINEITEM") or r.get("lineitem") or code,
                          "tags": r.get("tags") or ""})

    disc_rows = discovered_rows(args.discovered) + discovered_rows(getattr(args, "sweep", None))
    codes_planned = None
    planned_codes = []
    if disc_rows:
        kept, _ = screen_universe(disc_rows)
        codes_planned = len(kept)
        planned_codes = [{"ltcode": k["ltcode"], "name": k["name"]} for k in kept]
        meta = {k["ltcode"]: k for k in kept}
        for d in disc_rows:
            meta.setdefault(d.get("ltcode"), d)
        for li in lineitems:
            m = meta.get(li["ltcode"])
            if m:
                li["tags"] = m.get("tags") or li["tags"]
                li["lineitem"] = m.get("name") or li["lineitem"]

    guidance, outlook = [], []
    for src in (args.guidance or []):
        try:
            with open(src, encoding="utf-8") as fh:
                blob = json.load(fh)
        except (json.JSONDecodeError, OSError) as e:
            sys.stderr.write(f"skipped guidance {src}: {e}\n")
            continue
        g, o = _guidance_arrays(blob)
        guidance += g
        outlook += o
    seen_g = set()
    dedup_g = []
    for g in guidance:
        key = (g.get("LTCODE") or g.get("ltcode"), g.get("FISCALPERIOD") or g.get("fiscal_period"),
               g.get("GUIDE_TEXT") or g.get("guide_text"),
               g.get("SOURCE_PERIOD") or g.get("source_period"))
        if key in seen_g:
            continue
        seen_g.add(key)
        dedup_g.append(g)
    _seen_o, _dedup_o = set(), []
    for o in outlook:
        key = (o.get("LINEITEM") or o.get("lineitem"), o.get("FISCALPERIOD") or o.get("fiscal_period"),
               o.get("GUIDE_TEXT") or o.get("guide_text"), o.get("SOURCE_PERIOD") or o.get("source_period"))
        if key in _seen_o:
            continue
        _seen_o.add(key)
        _dedup_o.append(o)
    outlook = _dedup_o

    annual_rows = []
    for src in (getattr(args, "annual", None) or []):
        try:
            with open(src, encoding="utf-8") as fh:
                _rows_from_envelope(json.load(fh), annual_rows)
        except (json.JSONDecodeError, OSError) as e:
            sys.stderr.write(f"skipped annual {src}: {e}\n")
    call_dates = None
    if getattr(args, "call_dates", None) is not None:
        call_dates = []
        for src in args.call_dates:
            for _f, blob in load_responses([src]):
                for d in _walk_dicts(blob):
                    if d.get("CALL_DATE") or d.get("call_date"):
                        call_dates.append({"FISCALPERIOD": clean_period_label(
                            d.get("FISCALPERIOD") or d.get("fiscal_period")),
                            "CALL_DATE": str(d.get("CALL_DATE") or d.get("call_date"))[:10]})
        call_dates = sorted({(c["CALL_DATE"], c["FISCALPERIOD"]): c
                             for c in call_dates}.values(), key=lambda c: c["CALL_DATE"])
    _annual_absent = set()
    for src in (getattr(args, "annual", None) or []):
        for _f, blob in load_responses([src]):
            _absent_upstream(blob, _annual_absent)
    _annual_codes = {(r.get("LTCODE") or r.get("ltcode")) for r in annual_rows}
    _annual_absent |= {c for c in str(getattr(args, "annual_probed", "") or "").split(",")
                       if c and c not in _annual_codes}
    annual_rows = [r for r in annual_rows if re.fullmatch(
        r"(R\d*-)?(FY)?\d{4}", str(r.get("FISCALPERIOD") or r.get("fiscal_period") or "").strip().upper())]
    payload = {"ticker": args.ticker.upper(),
               "company": (_resolved or {}).get("company_name") or args.company,
               "ticker_id": args.ticker_id, "sector": args.sector,
               "latest_reported_quarter": args.latest_quarter.upper(),
               "report_date": args.report_date,
               "lineitems": lineitems, "actuals": rows,
               "codes_planned": codes_planned,
               "planned_codes": planned_codes,
               "codes_absent_upstream": sorted(absent),
               "codes_absent_detail": {c: ("annual_only" if c in {(r.get("LTCODE") or r.get("ltcode"))
                                                                  for r in annual_rows}
                                           else "no_data" if c in _annual_absent
                                           else "annual_not_probed") for c in sorted(absent)},
               "guidance": dedup_g, "outlook": outlook,
               "annual_actuals": annual_rows,
               "ticker_match": (_resolved if _resolved else
                                ({"match_type": args.match_type,
                                  "ticker_id": str(args.ticker_id), "source": "declared"}
                                 if getattr(args, "match_type", None) else None)),
               "call_dates": call_dates,
               "run_folder": os.path.basename(run_dir),
               "ingested_utc": (os.environ.get("INFLECTION_INGESTED_UTC")
                                or _utc_now().strftime("%Y-%m-%d"))}
    os.makedirs(os.path.dirname(os.path.abspath(args.out)) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=1)
    _unprobed = [c for c, v in payload["codes_absent_detail"].items() if v == "annual_not_probed"]
    print(json.dumps({"written": args.out, "files_read": len(files),
                      "annual_probe_needed": (
                          {"calls": _annual_calls(_unprobed, args.ticker_id, args.latest_quarter),
                           "note": "these codes returned nothing quarterly; send these calls "
                                   "exactly as printed (explicit limit, at most %d codes "
                                   "each), file the result, and re-run prepare so each is "
                                   "labelled annual-only or no data" % ANNUAL_CODES_PER_CALL}
                          if _unprobed else None),
                      "rows": len(rows), "duplicate_rows_dropped": _dupes,
                      "distinct_codes": len(lineitems),
                      "codes_planned": codes_planned,
                      "codes_absent_upstream": len(absent),
                      "empty_batches_absent_upstream": len(_empty_batches),
                      "guidance_rows": len(dedup_g), "outlook_rows": len(outlook),
                      "annual_rows": len(annual_rows)},
                     indent=2))


def cmd_verify(args):
    with open(args.raw, encoding="utf-8") as fh:
        raw = json.load(fh)

    planned = []
    src_rows = []
    src_rows += discovered_rows(args.discovered) + discovered_rows(getattr(args, "sweep", None))
    if src_rows:
        kept, _ = screen_universe(src_rows)
        planned = [k["ltcode"] for k in kept]
    else:
        planned = [r.get("ltcode") for r in (raw.get("planned_codes") or [])
                   if r.get("ltcode")]

    per_code, per_period = {}, {}
    for r in raw.get("actuals", []) or []:
        code = r.get("LTCODE") or r.get("ltcode")
        qed = r.get("QUARTERENDDATE") or r.get("quarter_end_date")
        if not code:
            continue
        per_code.setdefault(code, set()).add(qed)
        per_period[qed] = per_period.get(qed, 0) + 1

    fetched = set(per_code)
    missing = sorted(set(planned) - fetched) if planned else []
    unplanned = sorted(fetched - set(planned)) if planned else []
    thin = sorted((c, len(q)) for c, q in per_code.items() if len(q) < MIN_Q_FOR_RANK)
    periods = sorted(p for p in per_period if p)

    latest = str(raw.get("latest_reported_quarter") or "").upper()
    fatal = []
    if not raw.get("actuals"):
        fatal.append("raw.json contains no actual rows")
    _labs = [clean_period_label(r.get("FISCALPERIOD") or r.get("fiscal_period"))
             for r in raw.get("actuals") or []]
    _half = [x for x in _labs if re.match(r"^[12]H\d{2}$", x or "", re.I)]
    if _labs and len(_half) > len(_labs) / 2:
        fatal.append(HALF_YEAR_MESSAGE % _half[-1])
    if latest:
        labels = {clean_period_label(r.get("FISCALPERIOD") or r.get("fiscal_period"))
                  .upper() for r in raw.get("actuals", [])}
        if latest not in labels:
            fatal.append(f"no row carries the stated latest reported quarter {latest}; "
                         f"either the quarter is not reported or the fetch missed it")
    if len(periods) < MIN_Q_FOR_RANK:
        fatal.append(f"only {len(periods)} distinct quarters present, below the "
                     f"{MIN_Q_FOR_RANK}-quarter minimum to score an acceleration")
    if latest and re.fullmatch(r"[1-4]Q\d\d", latest):
        _lk = _quarter_sort_key(latest)
        _src = [str(g.get("SOURCE_PERIOD") or g.get("source_period") or "").upper()
                for g in (raw.get("guidance") or []) + (raw.get("outlook") or [])]
        _src = [x for x in _src if re.fullmatch(r"[1-4]Q\d\d", x)]
        _newest = max(_src, key=_quarter_sort_key) if _src else None
        if _newest and _quarter_sort_key(_newest) > _lk:
            fatal.append(f"the stated latest quarter {latest} is older than guidance the company "
                         f"issued with {_newest}: the source's latest-quarter field looks stale. "
                         f"Find the latest reported quarter with get_actuals (revenue, "
                         f"fiscal_period='{_newest},{next_quarter(_newest)}', an explicit "
                         f"limit) and re-plan with the newest quarter that returns a row")
        _ends = sorted({str(r.get("QUARTERENDDATE") or r.get("quarterenddate") or "")[:10]
                        for r in raw.get("actuals", [])
                        if clean_period_label(r.get("FISCALPERIOD") or r.get("fiscal_period")).upper() == latest} - {""})
        _today = str(raw.get("ingested_utc") or _utc_now().strftime("%Y-%m-%d"))[:10]
        if _ends:
            if _ends[-1] > _today:
                fatal.append(f"the latest quarter {latest} carries an end date in the future "
                             f"({_ends[-1]}); the source's period metadata is wrong")
            else:
                _age = (datetime.strptime(_today, "%Y-%m-%d") - datetime.strptime(_ends[-1], "%Y-%m-%d")).days
                if _age > 200 and not getattr(args, "allow_stale", False):
                    fatal.append(f"the latest quarter {latest} ended {_ends[-1]}, {_age} days ago; a "
                                 f"newer quarter has almost certainly been reported. Check with "
                                 f"get_actuals, or pass --allow-stale for a deliberate historical scan")
    _cd = raw.get("call_dates")
    _ends_all = sorted({str(r.get("QUARTERENDDATE") or r.get("quarterenddate") or "")[:10]
                        for r in raw.get("actuals", [])
                        if clean_period_label(r.get("FISCALPERIOD") or r.get("fiscal_period")).upper()
                        == latest} - {""}) if latest else []
    _fetch_day = str(raw.get("ingested_utc") or _utc_now().strftime("%Y-%m-%d"))[:10]
    if _cd is None:
        if not getattr(args, "allow_no_calendar", False):
            fatal.append("the earnings calendar was not fetched: run get_call_date(ticker_id, "
                         "start_date=<latest quarter end>, end_date=<+200 days>) and pass it to "
                         "ingest with --call-dates, so a newer reported quarter cannot go "
                         "unnoticed (--allow-no-calendar overrides)")
    elif _ends_all and not getattr(args, "allow_stale", False):
        _after = [c for c in _cd if _ends_all[-1] < str(c.get("CALL_DATE") or "")[:10] <= _fetch_day]
        if not _after and not getattr(args, "allow_no_calendar", False):
            fatal.append("the earnings calendar holds no call after %s ended (%s), so it "
                         "cannot show whether a newer quarter has reported. The query "
                         "returned nothing: re-run get_call_date(ticker_id, start_date=<%s>, "
                         "end_date=<+200 days>) and check the window; if the provider has no "
                         "calendar for this company, confirm the quarter with the user and pass "
                         "--allow-no-calendar" % (latest, _ends_all[-1], _ends_all[-1]))
        if len(_after) >= 2:
            fatal.append("%d earnings calls were held after %s ended (%s): a newer quarter "
                         "has been reported. Ask the user which quarter to scan; do not "
                         "build this one unasked" % (len(_after), latest, ", ".join(
                             "%s %s" % (c.get("FISCALPERIOD"), c.get("CALL_DATE")) for c in _after)))
    _tm = raw.get("ticker_match") or {}
    if not getattr(args, "allow_unverified_ticker", False):
        if not _tm.get("match_type"):
            fatal.append("how the ticker was resolved is not recorded; pass search_tickerid's "
                         "match_type to ingest with --match-type")
        elif _tm.get("match_type") not in EXACT_MATCH_TYPES:
            fatal.append("the ticker was resolved by a %s match, not an exact one; confirm the "
                         "company with the user before scanning" % _tm.get("match_type"))
    _tids = {str(r.get("TICKER_ID", r.get("ticker_id"))) for r in raw.get("actuals", [])
             if r.get("TICKER_ID", r.get("ticker_id")) is not None}
    if raw.get("ticker_id") and _tids - {str(raw.get("ticker_id"))}:
        fatal.append("raw.json holds rows for ticker_id(s) %s besides this run's %s"
                     % (", ".join(sorted(_tids - {str(raw.get("ticker_id"))})), raw.get("ticker_id")))
    _absent = set(raw.get("codes_absent_upstream") or [])
    if planned:
        planned = [c for c in planned if c not in _absent]
    if planned:
        got = len(fetched & set(planned))
        if got < PRIMARY_SUPPORTING_FLOOR * len(planned):
            fatal.append(
                f"only {got} of {len(planned)} planned codes returned data "
                f"({100.0 * got / len(planned):.0f}%), below the "
                f"{100 * PRIMARY_SUPPORTING_FLOOR:.0f}% floor. Re-run the fetch "
                f"before building: check the keyword sweep ran, that every batch "
                f"the plan printed was requested, and that the responses "
                f"directory held only this run's files")

    if not (raw.get("guidance") or raw.get("outlook")):
        if getattr(args, "allow_no_guidance", False):
            warned_no_guidance = True
        else:
            warned_no_guidance = False
            fatal.append(
                "no guidance and no outlook rows are present. Run step 3's "
                "guidance call before building: get_guidance(ticker_id, "
                "latest=true, limit='100'), paging on offset until has_more is "
                "false. If that returns nothing, run it again WITHOUT latest "
                "over the dated window - a company that does not guide has no "
                "guide dates, so `latest` hides the very notes that say so, and "
                "the note will otherwise report a company's stated refusal as a "
                "gap in this scan. Pass --allow-no-guidance once both calls have "
                "genuinely come back empty")
    else:
        warned_no_guidance = False

    out = {
        "ok": not fatal,
        "fatal": fatal,
        "guidance_step_overridden": warned_no_guidance,
        "codes_planned": len(planned),
        "codes_fetched": len(fetched),
        "codes_planned_but_missing": missing,
        "codes_fetched_but_unplanned": unplanned,
        "rows": len(raw.get("actuals", []) or []),
        "distinct_quarters": len(periods),
        "quarter_span": [periods[0], periods[-1]] if periods else [],
        "codes_with_thin_history": thin[:30],
        "guidance_rows": len(raw.get("guidance", []) or []),
        "outlook_rows": len(raw.get("outlook", []) or []),
        "ticker_match": _tm.get("match_type"),
        "calls_after_latest_quarter": (len([c for c in (_cd or []) if _ends_all and
                                            _ends_all[-1] < str(c.get("CALL_DATE") or "")[:10]
                                            <= _fetch_day]) if _cd is not None else None),
    }
    print(json.dumps(out, indent=2))
    if fatal:
        sys.exit(1)


def _unusual_word(z):
    a = round(abs(z or 0), 1)
    return ("very unusual" if a >= 2.5 else "unusual" if a >= 1.5
            else "notable" if a >= 0.75 else "routine")


def _fmt_row_line(r, rank):
    return (f"  {rank}. {r.get('display') or r['name']}  |  "
            f"{_unusual_word(r['z_display'])} for this line ({r['z_display']:+.1f})  |  "
            f"{_fmt_accel(r['latest_accel'], r['unit'])}  |  "
            f"Y/Y {_fmt_growth(r['latest_growth'], r['unit'])} from "
            f"{_fmt_growth(r['prev_growth'], r['unit'])}  |  {r['streak']}Q trend  |  "
            f"{r.get('implication')}"
            + (f"  |  {r['comp_flag'].replace('_', ' ')}" if r.get("comp_flag") else ""))


def cmd_chat(args):
    with open(args.computed, encoding="utf-8") as fh:
        p = json.load(fh)
    c, g = p["coverage"], (p.get("guidance_check") or {})
    pc = p.get("published_counts") or {"accelerating": c["material_accelerating"],
                                        "decelerating": c["material_decelerating"]}
    L = [f"{p['ticker']} inflection scan, latest reported quarter {p['latest_quarter']}.",
         f"Scanned {c['series_scanned']} series over {c['quarters']} quarters: "
         f"{pc['accelerating']} accelerating and {pc['decelerating']} "
         f"decelerating cleared the materiality bar, {c['series_excluded']} held out."]
    if p.get("guard"):
        L.append("GUARD: " + p["guard"])
    if p["accelerating"]:
        L.append("")
        L.append("Accelerating:")
        L += [_fmt_row_line(r, i) for i, r in enumerate(p["accelerating"][:3], 1)]
    if p["decelerating"]:
        L.append("")
        L.append("Decelerating:")
        L += [_fmt_row_line(r, i) for i, r in enumerate(p["decelerating"][:3], 1)]
    contra = ([r for r in g.get("scored", []) if r["verdict"] == "turns negative"]
              or [r for r in g.get("scored", []) if r["verdict"] == "continues at a slower rate"])
    _lvl = {r.get("ltcode") for r in contra if not r.get("yoy_form")}
    contra = [r for r in contra if not (r.get("yoy_form") and r.get("resolved_ltcode") in _lvl)]
    if contra:
        L.append("")
        L.append("Guidance continuation test:")
        for r in contra[:3]:
            L.append(f"  {display_name(r['name'])}  |  {r['verdict']}  |  guide implies "
                     f"{_fmt_growth(r.get('guide_implied_growth'), r.get('unit','pct'))} "
                     f"versus {_fmt_growth(r.get('latest_growth'), r.get('unit','pct'))} now "
                     f"({_fmt_accel(r.get('guide_implied_accel'), r.get('unit','pct'))})")
    if p.get("divergences"):
        L.append("")
        L.append("Divergences: " + "; ".join(d["headline"] for d in p["divergences"]))
    if p.get("quiet_headline"):
        L.append("")
        L.append("Headline lines scanned and quiet: "
                 + ", ".join(f"{r['name']} "
                             f"({_fmt_growth(r['latest_growth'], r['unit'])})"
                             for r in p["quiet_headline"][:6]))
    print("\n".join(L))


SCHEMA_DOC = """
raw.json contract (write MCP responses in verbatim, one row per observation)

{
  "ticker": "STRM",
  "company": "Lumen Stream Media Co",
  "ticker_id": "90006",
  "sector": "Technology",
  "latest_reported_quarter": "1Q27",
  "report_date": "2026-05-27",
  "lineitems": [
    {"ltcode": "IS_P_101", "lineitem": "Total Revenue", "tags": "top_kpi|industry_kpi"}
  ],
  "actuals": [
    {"LTCODE": "IS_P_101", "LINEITEM": "Total Revenue", "FISCALPERIOD": "2Q27",
     "QUARTERENDDATE": "2026-07-31", "VALUE": "$12.3456B"}
  ]
}

Notes
- "actuals" rows go in EXACTLY as get_actuals returns them. Live rows carry
  LTCODE, LINEITEM, FISCALPERIOD, QUARTERENDDATE and VALUE, and nothing else:
  there is no scale, currency or data_source column, because VALUE is a
  pre-formatted string with the currency symbol and scale suffix baked in
  ("$12.3456B", "$896.0000M", "75.02%"). parse_value normalises it.
- Upper or lower case keys both work (LTCODE / ltcode, VALUE / value).
- QUARTERENDDATE is the sorting authority; FISCALPERIOD is a display label and
  may carry an "R-" revision prefix, which is stripped.
- A row whose data_source IS present and is not "actual" is dropped: an estimate
  can never enter an inflection calculation. Absent, the row is taken as actual,
  which is what get_actuals returns by default.
- A repeated QUARTERENDDATE for one code overwrites the earlier revision.
- Rather than assembling this by hand, run:
    python compute.py ingest <response files...> --ticker T --latest-quarter Q \\
           --discovered scratch/discovered.json --out scratch/raw.json
"""


def cmd_check(args):
    import shutil as _sh
    here = os.path.dirname(os.path.abspath(__file__))
    need = ["compute.py", "render.py", "format_lint.py", "qc_summary.py", "ground_truth.py",
            "run_pipeline.py", "binding.py", "_contract.py"]
    missing = [f for f in need if not os.path.exists(os.path.join(here, f))]
    _logo = os.path.join(os.path.dirname(here), "assets", "insync_mark_b64.txt")
    if not os.path.exists(_logo):
        missing.append("assets/insync_mark_b64.txt")
    pdf = None
    try:
        import weasyprint
        pdf = "weasyprint"
    except Exception:
        for exe in ("chromium", "chromium-browser", "google-chrome", "chrome", "wkhtmltopdf"):
            if _sh.which(exe) or os.path.exists("/opt/pw-browsers/" + exe):
                pdf = exe
                break
    _root = os.path.dirname(here)
    _seen_v = {"compute.py": VERSION}
    try:
        _m = re.search(r"^version:\s*([\d.]+)", open(os.path.join(_root, "SKILL.md"), encoding="utf-8").read(), re.M)
        _seen_v["SKILL.md"] = _m.group(1) if _m else None
    except OSError:
        _seen_v["SKILL.md"] = None
    try:
        _m = re.search(r"\*Version ([\d.]+)\*", open(os.path.join(_root, "README.md"), encoding="utf-8").read())
        _seen_v["README.md"] = _m.group(1) if _m else None
    except OSError:
        _seen_v["README.md"] = None
    try:
        _m = re.search(r"^(?:#+\s*|\*\*)v?(\d+\.\d+\.\d+)",
                       open(os.path.join(_root, "references", "changelog.md"), encoding="utf-8").read(), re.M)
        _seen_v["references/changelog.md"] = _m.group(1) if _m else None
    except OSError:
        pass
    _vdrift = {k: v for k, v in _seen_v.items() if v != VERSION}
    out = {
        "engine_complete": not missing,
        "engine_version": VERSION,
        "version_consistent": not _vdrift,
        "version_drift": _vdrift,
        "polarity_registry_codes": len(POLARITY_REGISTRY),
        "missing_scripts": missing,
        "python": sys.version.split()[0],
        "python_ok": sys.version_info >= (3, 9),
        "pdf_engine": pdf,
        "pdf_text_check": bool(_sh.which("pdftotext")),
        "instruction": (
            "STOP: the skill was installed without its engine (%s missing). Do not compute "
            "any figure yourself. Tell the user to install the full inflection-monitor zip."
            % ", ".join(missing)) if missing else (
            ("STOP: no PDF engine (weasyprint, Chromium or wkhtmltopdf) is available. "
             "The PDF is the only deliverable, so there is nothing to deliver: tell the "
             "user a PDF engine is needed. Do not ship the HTML.") if not pdf else
            "OK" + ("" if _sh.which("pdftotext") else
                    "; pdftotext is absent, so the PDF-text gates will not run and the QC "
                    "summary will say so")),
    }
    print(json.dumps(out, indent=1))
    if missing or not out["python_ok"]:
        sys.exit(3)
    if not pdf:
        sys.exit(4)
    if _vdrift:
        sys.exit(5)


def main():
    ap = argparse.ArgumentParser(description="inflection-monitor compute engine")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("plan", help="emit the fetch plan (offline)")
    p.add_argument("--ticker", required=True)
    p.add_argument("--latest-quarter", required=True)
    p.add_argument("--discovered", nargs="+",
                   help="raw search_lineitem(queries='all') response JSON")
    p.add_argument("--sweep", nargs="+",
                   help="raw keyword search_lineitem response file(s): one per bank, a "
                        "list of responses, or one merged envelope - all are read")
    p.add_argument("--allow-spine", action="store_true",
                   help="plan with fewer than 10 discovered codes (falls back to the "
                        "six spine codes); the default refuses an empty discovery")
    p.add_argument("--subindustry", help="insync_subindustry, to pick a sector keyword bank")
    p.add_argument("--ticker-id", help="numeric ticker_id, threaded into the printed searches")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_plan)

    b = sub.add_parser("build", help="compute everything from raw.json")
    b.add_argument("raw")
    b.add_argument("--out-dir", default="./out")
    b.add_argument("--commentary",
                   help="the same commentary map passed to render.py; enables the "
                        "five-way management-commentary standing per metric")
    b.add_argument("--prepared-by",
                   default=os.environ.get("INFLECTION_PREPARED_BY"),
                   help="name recorded as the preparer of this run and printed "
                        "in the masthead. Defaults to $INFLECTION_PREPARED_BY.")
    b.add_argument("--allow-partial", action="store_true",
                   help="build a note whose primary and supporting coverage is "
                        "below the %d%% floor. The refusal is the default "
                        "because a note that thin is a failed fetch, not a "
                        "qualified finding; this is the deliberate override, "
                        "and the page then carries the partial-scan label."
                        % int(PRIMARY_SUPPORTING_FLOOR * 100))
    b.set_defaults(func=cmd_build)

    g = sub.add_parser("ingest", help="merge raw MCP responses into raw.json")
    g.add_argument("files", nargs="+",
                   help="get_actuals response JSON files, or directories of them")
    g.add_argument("--out", default="scratch/raw.json")
    g.add_argument("--ticker", required=True)
    g.add_argument("--latest-quarter", required=True)
    g.add_argument("--company")
    g.add_argument("--ticker-id", required=True,
                   help="numeric ticker_id from search_tickerid; any row carrying a "
                        "different TICKER_ID stops the run")
    g.add_argument("--match-type",
                   help="search_tickerid's match_type for the chosen ticker_id "
                        "(exact_ticker or exact_company); recorded for verify")
    g.add_argument("--resolve", nargs="*", default=None,
                   help="v4.54.0 (C6): the saved search_tickerid response. When given, the "
                        "company name, match type and ticker are read from it, and a "
                        "--company, --ticker or --match-type that disagrees stops the run")
    g.add_argument("--call-dates", nargs="*", default=None,
                   help="get_call_date response file(s): the earnings calendar from "
                        "the latest quarter's end, used by verify's stale-quarter check")
    g.add_argument("--sector")
    g.add_argument("--report-date")
    g.add_argument("--discovered", nargs="+", help="search_lineitem response, for names and tags")
    g.add_argument("--sweep", nargs="+", help="keyword search_lineitem response(s), for names and tags")
    g.add_argument("--guidance", nargs="*", default=[],
                   help="get_guidance response file(s); arrays are extracted for you")
    g.add_argument("--annual", nargs="*", default=[],
                   help="get_actuals responses for the plan's annual_batches (full-year "
                        "figures, used only to prove a cash-flow line's period)")
    g.add_argument("--annual-probed", default="",
                   help="v4.56.0: comma-separated codes whose printed annual probe was sent and "
                        "answered; any of them with no annual row is labelled no data in the feed")
    g.set_defaults(func=cmd_ingest)

    v = sub.add_parser("verify", help="audit raw.json coverage before computing")
    v.add_argument("raw")
    v.add_argument("--allow-stale", action="store_true",
                   help="deliberate historical scan: accept a latest quarter that ended over "
                        "200 days before the data was fetched")
    v.add_argument("--discovered", nargs="+", help="search_lineitem response, to check against the plan")
    v.add_argument("--sweep", nargs="+", help="keyword search_lineitem response(s), to check against the plan")
    v.add_argument("--allow-no-guidance", action="store_true",
                   help="proceed with a raw.json that carries no guidance and no "
                        "outlook rows. The refusal is the default because an "
                        "unfetched step and a company with nothing to say produce "
                        "the same empty arrays, and the note says something "
                        "different for each; pass this only once the guidance "
                        "call has been run BOTH with latest=true and without it")
    v.add_argument("--allow-no-calendar", action="store_true",
                   help="proceed without the get_call_date earnings calendar")
    v.add_argument("--allow-unverified-ticker", action="store_true",
                   help="proceed with a raw.json that records no exact ticker match")
    v.set_defaults(func=cmd_verify)

    dv = sub.add_parser("dives", help="print the deep-dive lines for the commentary step "
                                      "(runs the engine in memory; writes nothing)")
    dv.add_argument("raw")
    dv.add_argument("--allow-partial", action="store_true")
    dv.set_defaults(func=cmd_dives)

    ch = sub.add_parser("chat", help="emit the chat-ready summary, verbatim")
    ch.add_argument("computed")
    ch.set_defaults(func=cmd_chat)

    s = sub.add_parser("schema", help="print the raw.json contract")
    s.set_defaults(func=lambda a: print(SCHEMA_DOC))

    ck = sub.add_parser("check", help="confirm the skill is installed whole and say what this "
                                      "environment can produce (run before anything else)")
    ck.set_defaults(func=cmd_check)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    try:
        from _contract import run as _run_contract
    except ImportError:
        print(json.dumps({"status": "error", "stage": "check",
                          "message": "scripts/_contract.py is missing: the skill was installed "
                                     "without its engine. Install the full zip."}))
        sys.exit(3)
    _run_contract(main, (sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("-") else "compute"))
