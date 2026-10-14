#!/usr/bin/env python3
"""earnings-packet-3p, sector resolution as CODE rather than as a table to be read.

    python3 references/sector.py --industry "Banking" --subindustry "Diversified Banks"
    python3 references/sector.py --industry "Software" --subindustry "Application Software" \
                                 --loss-making            # or --rev-growth 24.1
    python3 references/sector.py --selftest

WHY THIS EXISTS
---------------
`sector-config.md` is the AUTHORITY and stays the authority: it carries the reasoning,
the coverage limits and the B9 / B10 / B12 ladders, and an editor reads it. But an
operator resolving one issuer needed to read all of it to use roughly a twentieth of it,
and the resolution itself is a first-match-wins walk over two ordered tables, which is
exactly the kind of thing a person or a model gets subtly wrong and a function does not.

So the WALK is here and the REASONING is there. This prints the resolved key and only
that key's spine, drivers and lens. Ambiguity is reported, never guessed.

THE ORDERING RULES ARE LOAD-BEARING, and --selftest holds each one:
  * `industry` resolves before any subindustry keyword. `Electrical Equipment` contains
    `electric`, so a keyword-first walk hands every electrical-equipment maker a utility
    rate-base spine.
  * the bare keyword `gas` is NOT in the energy list. `Gas and Water Utilities` contains
    it, and a water utility handed a production-volume spine is the bug it caused.
  * `Real Estate` is deliberately NOT in the industry table: the industry carries
    operators, developers and services businesses, none of them FFO vehicles.
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

INDUSTRY = [
    ("Banking",            "banking"),
    ("Insurance",          "insurance"),
    ("Financial Services", "financials_nonbank"),
    ("Utilities",          "utilities"),
    ("Materials",          "energy_materials"),
    ("Oil and Gas",        "energy_materials"),
    ("Telecommunications", "telecom"),
    ("Industrial Products", "default"),
]

SUBINDUSTRY = [
    (["reit", "real estate investment trust"],                      "reit",              None),
    (["bank", "thrift", "diversified financial", "capital markets"], "banking",          None),
    (["insurance", "reinsurance", "p&c"],                           "insurance",         None),
    (["oil", "coal", "mining", "metals", "chemicals", "paper",
      "steel", "packaging", "construction materials"],              "energy_materials",  None),
    (["electric", "gas utility", "water", "multi-utility", "power"], "utilities",        None),
    (["software"],                                                  "software_growth",   "growth"),
    (["software"],                                                  "software_mature",   None),
    (["internet"],                                                  "internet_platform", None),
    (["it services"],                                               "software_growth",   "growth"),
]

PROVISIONAL = {"software_growth", "internet_platform"}

NO_EBIT = {"banking", "financials_nonbank", "insurance", "reit"}

NO_EV = {"banking", "financials_nonbank", "insurance"}


def load_table():
    with open(os.path.join(HERE, "sector_config.json"), encoding="utf-8") as fh:
        return json.load(fh)


def resolve(industry, subindustry, loss_making=False, rev_growth=None):
    ind = (industry or "").strip()
    sub = (subindustry or "").strip().lower()
    growth = bool(loss_making) or (rev_growth is not None and rev_growth > 20.0)

    for label, key in INDUSTRY:
        if ind.lower() == label.lower():
            return key, "industry test: " + label, False

    for words, key, gate in SUBINDUSTRY:
        hit = next((w for w in words if w in sub), None)
        if hit is None:
            continue
        if gate == "growth" and not growth:
            continue
        return key, "subindustry keyword: " + hit, False

    return "default", "no rule matched", True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--industry", default="")
    ap.add_argument("--subindustry", default="")
    ap.add_argument("--loss-making", action="store_true")
    ap.add_argument("--rev-growth", type=float, default=None)
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()

    if a.selftest:
        return selftest()

    if not a.industry and not a.subindustry:
        print("supply --industry and/or --subindustry, or --selftest", file=sys.stderr)
        return 2

    key, how, ambiguous = resolve(a.industry, a.subindustry, a.loss_making, a.rev_growth)
    cfg = load_table()[key]

    print("sector_key       " + key)
    print("resolved by      " + how)
    print("model spine adds " + cfg["spine_adds"])
    print("driver KPIs      " + cfg["drivers"])
    print("valuation lens   " + ", ".join(cfg["lens"]) + "   (sections 7 AND 8, this order)")
    print("universal spine  revenue, gross profit + margin, operating profit + margin, "
          "EBITDA + margin, net income, diluted EPS, operating cash flow, free cash flow, "
          "net debt")
    if ambiguous:
        print("WARNING          no rule matched; default is never wrong, only sometimes "
              "incomplete. Say so in the report.")
    if key in PROVISIONAL:
        print("PROVISIONAL      confirm at the Step 2 probe that the issuer returns rows "
              "for a NAMED recurring-revenue code (or a volume measure for "
              "internet_platform). The spine follows the code that satisfied the gate: "
              "sector-config.md section B9 is the ladder. Fall back to software_mature, "
              "or default if it is not a software business. Record the branch in warnings.")
    if key in NO_EBIT:
        print("NO EBIT          two of the six locked row 1 tiles have no defined content "
              "here. Take the named substitute from sector-config.md B12, label BOTH tiles "
              "with the measure, take both from the same rung, record it in warnings.")
    if key in NO_EV:
        print("NO EV            enterprise value is undefined; EV/EBITDA and EV/Sales are "
              "excluded outright, not de-emphasised. The pre-profit ladder (B10) takes "
              "P/B then P/Sales here and stops.")
    return 0


def selftest():
    cases = [
        ("Industrial Products", "Electrical Equipment", False, None, "default",
         "industry beats keyword: `Electrical Equipment` contains `electric`"),
        ("Utilities", "Gas and Water Utilities", False, None, "utilities",
         "a gas-and-water utility is a utility, not energy"),
        ("", "Gas and Water Utilities", False, None, "utilities",
         "even with no industry field, bare `gas` must not route to energy"),
        ("", "Diversified REITs", False, None, "reit",
         "REIT is a subindustry test because Real Estate is not in the industry table"),
        ("Real Estate", "Real Estate Services", False, None, "default",
         "a real-estate SERVICES business is not an FFO vehicle"),
        ("Banking", "Diversified Banks", False, None, "banking", "industry test"),
        ("Insurance", "Life and Health Insurance", False, None, "insurance",
         "`life` is gone from the keyword list; the industry test catches every insurer"),
        ("Financial Services", "Asset Management", False, None, "financials_nonbank",
         "brokers, exchanges and asset managers are not banks and carry no EV multiple"),
        ("", "Application Software", True, None, "software_growth", "loss-making gate"),
        ("", "Application Software", False, 24.1, "software_growth", "growth gate above 20"),
        ("", "Application Software", False, 8.0, "software_mature", "growth gate below 20"),
        ("", "Application Software", False, None, "software_mature", "no gate data, mature"),
        ("", "Internet Retail", False, None, "internet_platform", "internet keyword"),
        ("", "Interactive Media", False, None, "default",
         "the keyword is `internet`; `Interactive Media` does not contain it, and the map\n         is faithful to sector-config.md rather than guessing wider"),
        ("", "IT Services", False, 31.0, "software_growth", "IT services with the gate"),
        ("", "IT Services", False, 3.0, "default", "IT services without the gate"),
        ("Oil and Gas", "Integrated Oil", False, None, "energy_materials", "industry test"),
        ("Telecommunications", "Wireless", False, None, "telecom", "industry test"),
        ("", "Aerospace and Defense", False, None, "default", "unmatched falls to default"),
    ]
    tbl = load_table()
    bad = 0
    for ind, sub, loss, gr, want, why in cases:
        got, how, _ = resolve(ind, sub, loss, gr)
        okk = got == want
        bad += 0 if okk else 1
        print(("  ok   " if okk else "  FAIL ") + f"{want:19s} <- {ind or '-'} / {sub}")
        if not okk:
            print(f"         got {got} via {how}   ({why})")

    for key in tbl:
        if key not in ("default",) and not tbl[key]["lens"]:
            print("  FAIL  empty lens for " + key); bad += 1
    for key in NO_EV:
        if any(m.startswith("EV/") for m in tbl[key]["lens"]):
            print("  FAIL  EV multiple in the lens of " + key); bad += 1
    missing = {k for _, k in INDUSTRY} | {k for _, k, _ in SUBINDUSTRY} | {"default"}
    missing -= set(tbl)
    if missing:
        print("  FAIL  keys resolvable but absent from sector_config.json: "
              + ", ".join(sorted(missing))); bad += 1

    print(("SECTOR SELFTEST PASSED" if not bad else f"SECTOR SELFTEST FAILED ({bad})"))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
