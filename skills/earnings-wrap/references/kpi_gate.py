#!/usr/bin/env python3
"""
KPI-relevance build gate for the Earnings Wrap.

Source of truth: the LIVE InSync MCP KPI universe resolved by the Step 0 ladder
(mcp_top_kpi -> mcp_kpi_tier -> mcp_discovery). Checks that the wrap's declared Primary
KPI (the "<name> (Primary KPI)" tag in the Event Snapshot) is a real KPI for the company,
and picks that Primary KPI mechanically when several universe entries compete.

There is NO local KPI file and no file-based mode. Every KPI name and every KPI number in
a wrap traces to a live MCP call recorded in the config, so KPI relevance cannot drift with
a stale spreadsheet and cannot differ between two installs.

Tiers:
  PASS  (exit 0) - matches a member of the live MCP KPI universe.
  WARN  (exit 0) - matches, with provenance mcp_discovery: legitimate tier, but the note
                   must disclose it in kpiCap and sources.
  FAIL  (exit 2) - matches no member, OR the universe is empty (the ladder was not run).

Usage:
  python kpi_gate.py --check-mcp "Primary KPI name" "name1;name2;..." [provenance]
  python kpi_gate.py --pick-primary "name~tags;name~tags;..."
  python kpi_gate.py --pick-secondary "name~tags;name~tags;..."
  python kpi_gate.py --selftest-mcp
"""
import re, sys

STOP = {"total","market","the","and","for","per","adj","non","gaap","revenue","sales",
        "rate","margin","quarterly","ending","net","inc","corp","company","of","ex","gas",
        "consensus","tracked","actual","primary","kpi"}

def toks(s):
    return [t for t in re.split(r"[^a-z0-9]+", (s or "").lower()) if len(t) >= 4 and t not in STOP]

def overlap(a, b):
    for x in a:
        for y in b:
            if x == y:
                return True
            n = 0
            while n < len(x) and n < len(y) and x[n] == y[n]:
                n += 1
            if n >= 5:
                return True
    return False


TAG_RANK = ["driver_kpi", "earnings_flash_kpi", "company_kpi", "industry_kpi"]

def pick_secondary_kpi(entries):
    if not entries:
        return (None, "empty universe")
    primary, _ = pick_primary_kpi(entries)
    rest = [e for e in entries if e.get("name") != primary]
    if not rest:
        return (None, "universe holds only the Primary KPI")
    name, why = pick_primary_kpi(rest)
    return (name, "primary %r removed; %s" % (primary, why))


def pick_primary_kpi(entries):
    if not entries:
        return (None, "empty universe")
    def tags(e):
        return [t for t in str(e.get("tags", "")).split("|") if t]
    if not any(tags(e) for e in entries):
        return (None, "TAGLESS UNIVERSE - cannot be ranked mechanically. Every entry carries no "
                      "tag, so the tie-break has nothing to rank on and would return whichever "
                      "name you listed first; at Tier 3 that order is one you chose, not one the "
                      "MCP returned. Name the driver yourself and record the reason in "
                      "`primaryKpiBasis` (required at kpiProvenance mcp_discovery). Pick the line "
                      "the issuer's own results statement leads on.")
    pool, why = list(entries), "no ranked tag present, whole universe considered"
    for tag in TAG_RANK:
        hit = [e for e in entries if tag in tags(e)]
        if hit:
            pool, why = hit, "highest-priority tag present is %s (%d candidate%s)" % (
                tag, len(hit), "" if len(hit) == 1 else "s")
            break
    best = max(range(len(pool)), key=lambda i: (len(tags(pool[i])), -i))
    e = pool[best]
    return (e["name"], "%s; won on tag count %d, MCP order index %d"
            % (why, len(tags(e)), entries.index(e)))

def _norm(s):
    return re.sub(r"[^a-z0-9]", "", str(s).lower())

MCP_TIERS = ("mcp_top_kpi", "mcp_kpi_tier", "mcp_discovery")

def classify_mcp(wrap_primary, universe, provenance="mcp_top_kpi"):
    prov = (provenance or "mcp_top_kpi").strip().lower()
    if prov not in MCP_TIERS:
        return ("FAIL", wrap_primary, None, prov,
                "unknown provenance: expected one of " + ", ".join(MCP_TIERS))
    if not [u for u in universe if str(u).strip()]:
        return ("FAIL", wrap_primary, None, prov,
                "EMPTY MCP KPI universe - run the Step 0 ladder (top_kpi -> all tiers -> "
                "keyword discovery proven against get_actuals) before building")
    wt = toks(wrap_primary); wn = _norm(wrap_primary)
    tier = "WARN" if prov == "mcp_discovery" else "PASS"
    _specific = toks(wrap_primary)
    if not _specific and len(re.split(r"[^A-Za-z0-9]+", str(wrap_primary or "").strip())) < 2:
        return ("FAIL", wrap_primary, None, prov,
                "%r is a single generic term, not an operating driver. The Primary KPI names "
                "the ONE line that explains this quarter for this issuer - a segment, a "
                "geography, a product line, a volume or a rate - so it reads like 'Data Center "
                "Revenue' or 'Regulated Electric Revenue', never 'Revenue'. Pick it "
                "mechanically from the tagged universe with --pick-primary."
                % wrap_primary)
    for name in universe:
        if wn and wn == _norm(name):
            return (tier, wrap_primary, name, prov, "")
    for name in universe:
        nn = _norm(name)
        if wn and nn and (wn in nn or nn in wn):
            return (tier, wrap_primary, name, prov, "")
    for name in universe:
        if overlap(wt, toks(name)):
            return (tier, wrap_primary, name, prov, "")
    return ("FAIL", wrap_primary, None, prov, "matches no member of the MCP KPI universe")

def report_mcp(tier, wrap_primary, matched, provenance, why):
    tag = {"PASS":"[PASS]","WARN":"[WARN]","FAIL":"[FAIL]"}[tier]
    print(f"{tag} wrap Primary KPI: {wrap_primary!r}")
    print(f"       MCP KPI provenance: {provenance}")
    if tier == "PASS":
        print(f"       -> matches a live MCP KPI: {matched!r}.")
    elif tier == "WARN":
        print(f"       -> matches a discovered MCP line item: {matched!r}.")
        print("       -> tier mcp_discovery is allowed; disclose it in kpiCap and sources.")
    else:
        print(f"       -> {why} -> BUILD BLOCKED.")


if __name__ == "__main__":
    argv = sys.argv[1:]
    if argv[:1] == ["--check-mcp"]:
        if len(argv) < 3:
            print('usage: kpi_gate.py --check-mcp "<Primary KPI>" "<name1;name2;...>" [provenance]')
            sys.exit(1)
        name = argv[1]
        universe = [u.strip() for u in argv[2].split(";") if u.strip()]
        provenance = argv[3] if len(argv) > 3 else "mcp_top_kpi"
        tier, wp, matched, prov, why = classify_mcp(name, universe, provenance)
        report_mcp(tier, wp, matched, prov, why)
        print(f"       MCP KPI universe ({len(universe)}): {universe}")
        sys.exit(2 if tier == "FAIL" else 0)
    if argv[:1] == ["--pick-primary"]:
        if len(argv) < 2:
            print('usage: kpi_gate.py --pick-primary "<name~tags;name~tags;...>"'); sys.exit(1)
        ents = [{"name": p.split("~")[0], "tags": (p.split("~")[1] if "~" in p else "")}
                for p in argv[1].split(";") if p.strip()]
        nm, why = pick_primary_kpi(ents)
        print("PRIMARY KPI: %s\n     reason: %s" % (nm, why))
        sys.exit(0 if nm else 2)
    if argv[:1] == ["--pick-secondary"]:
        if len(argv) < 2:
            print('usage: kpi_gate.py --pick-secondary "<name~tags;name~tags;...>"'); sys.exit(1)
        ents = [{"name": p.split("~")[0], "tags": (p.split("~")[1] if "~" in p else "")}
                for p in argv[1].split(";") if p.strip()]
        nm, why = pick_secondary_kpi(ents)
        print("SECONDARY KPI: %s\n     reason: %s" % (nm, why))
        sys.exit(0 if nm else 2)
    if argv[:1] == ["--selftest-mcp"]:
        uni = ["Data Center - Revenue By Segment", "Networking - Data Center Revenue"]
        disc = ["Bookings Value", "EUV - Revenue", "System - Revenue", "Backlog Value"]
        cases = [
            ("real KPI, top tier",             "Data Center revenue", uni,  "mcp_top_kpi",  "PASS"),
            ("fabricated KPI",                 "Bitcoin holdings",    uni,  "mcp_top_kpi",  "FAIL"),
            ("real KPI, all-tier fallback",    "Networking revenue",  uni,  "mcp_kpi_tier", "PASS"),
            ("discovered KPI, disclosed",      "Bookings Value",      disc, "mcp_discovery","WARN"),
            ("discovered universe, bad KPI",   "Bitcoin holdings",    disc, "mcp_discovery","FAIL"),
            ("EMPTY universe (ladder skipped)","Bookings Value",      [],   "mcp_discovery","FAIL"),
            ("file-based provenance rejected", "Bookings Value",      disc, "local_file",   "FAIL"),
        ]
        bad = 0
        for label, kpi, u, prov, want in cases:
            tier, wp, matched, pr, why = classify_mcp(kpi, u, prov)
            ok = (tier == want)
            print(f"{'ok  ' if ok else 'BAD '} {label:34s} want={want:4s} got={tier}")
            if not ok:
                bad = 1
        print("\nRESULT:", "ladder gate behaves as specified" if not bad else "LADDER GATE REGRESSION")
        sys.exit(bad)
    print('usage: kpi_gate.py --check-mcp "<Primary KPI>" "<name1;name2;...>" [provenance]')
    print('       kpi_gate.py --pick-primary "<name~tags;name~tags;...>"')
    print("       kpi_gate.py --selftest-mcp")
    sys.exit(1)
