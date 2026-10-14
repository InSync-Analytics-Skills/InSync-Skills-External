#!/usr/bin/env python3
"""
sector_brief.py - print ONLY the rules that apply to one company's sector.

Why this exists. `kpi-selection-standard.md` is the authoritative sector standard and it is
roughly 400 lines: 50 sector rows, 30 Tier-3 keyword rows, three ltcode-level spines and a
driver map. A build needs ONE row of each of those. Reading the whole file to find them costs
about 9,000 tokens of context on every run and returns 29 sectors the issuer is not in, which
is also 29 chances to pick up a neighbouring sector's KPI by mistake.

So this reads the same two files the gates read and prints the matched slice:

  python references/sector_brief.py "Electric Utilities"
  python references/sector_brief.py subindustry.txt          # a file works too

It is a READER, never a second source of truth. The sector row comes from
`common/sectors.json` (what `sector_resolve.py` and `sector_gate` use) and the keyword and
spine blocks are sliced out of `kpi-selection-standard.md` by heading, so neither can drift
from the standard: there is no copy here to fall out of date. If the standard gains a row,
this prints it the same day.

Exit 0 with a matched row, 3 when nothing matched (read the standard in full, and expect the
`default` spine to be disclosed on the page).
"""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SECTORS = os.path.join(HERE, "..", "common", "sectors.json")
STANDARD = os.path.join(HERE, "kpi-selection-standard.md")

SPINES = [
    ("### Sector spine: Telecommunications", ("telecom", "wireless", "cable")),
    ("### Sector spine: Asset Management", ("asset manage",)),
    ("### Sector spine: Financials and Insurance",
     ("bank", "financ", "insur", "capital market", "brokerage")),
    ("### Sector spine: Utilities (and capital-intensive, regulated, income sectors)",
     ("utilit", "pipeline", "midstream", "energy", "oil", "gas", "renewable")),
    ("### Sector spine: REITs", ("reit", "real estate")),
]


def _slice(text, start, stops=("\n## ", "\n### ")):
    i = text.find(start)
    if i < 0:
        return ""
    j = len(text)
    for stop in stops:
        k = text.find(stop, i + len(start))
        if 0 <= k < j:
            j = k
    return text[i:j].strip()


def _keyword_row(text, sub):
    block = _slice(text, "## Tier 3 discovery keyword sets")
    rows = [ln for ln in block.split("\n") if ln.startswith("|")]
    if not rows:
        return None, []
    header, out = rows[:2], []
    low = (sub or "").lower()
    for ln in rows[2:]:
        key = ln.split("|")[1].strip().lower()
        if not key or set(key) <= set("-: "):
            continue
        for part in re.split(r"\s*/\s*", key):
            part = part.strip()
            if not part or part == "default":
                continue
            for cand in {part, part.replace("&", "and"), part.replace(" and ", " & ")}:
                if cand and cand in low:
                    out.append(ln)
                    break
            else:
                continue
            break
    return header, out


def brief(sub):
    doc = json.load(open(SECTORS, encoding="utf-8"))
    text = open(STANDARD, encoding="utf-8").read()
    low = (sub or "").lower()
    row = None
    for r in doc["rows"]:
        if any(m.lower() in low for m in r["match"]):
            row = r
            break

    print("SECTOR BRIEF for insync_subindustry = %r" % sub)
    print("=" * 78)
    if row:
        print("\n[1] SECTOR ROW  (common/sectors.json, id=%s)" % row["id"])
        print("  Primary margin metric   : %s" % row["margin"])
        print("  Guidance variable       : %s" % row["guidance"])
        print("  Core investment debate  : %s" % row["debate"])
        print("\n  Decisions 2 to 4 still come from sector_resolve.py, which proves the margin")
        print("  ltcode against get_actuals and takes the guidance variable from what the issuer")
        print("  actually guides. This row is what it matches, not a substitute for running it.")
    else:
        d = doc["default"]
        print("\n[1] NO SECTOR ROW MATCHED. The generic spine is in use and the renderer will")
        print("    disclose it in the Section 6 caption. Read kpi-selection-standard.md in full")
        print("    before relying on the default.")
        print("  Default margin          : %s" % d["margin"])
        print("  Default guidance        : %s" % d["guidance"])
        print("  Default debate          : %s" % d["debate"])

    header, rows = _keyword_row(text, sub)
    print("\n[2] TIER 3 DISCOVERY KEYWORDS  (only needed at kpiProvenance = mcp_discovery)")
    if rows:
        malformed = [ln for ln in rows if ln.count("|") - 1 >= 4]
        for ln in header + rows:
            print("  " + ln)
        if malformed:
            print("\n  NOTE: the row above carries the sector-rules columns (margin | guidance |")
            print("  debate), not a keyword list - 18 of the 30 rows in the standard do. Do NOT")
            print("  pass it to search_lineitem. Use these instead, derived from the sector row")
            print("  in common/sectors.json (v7.7.4; deterministic, same row in same list out):")
            _src = row if row else doc["default"]
            _kw = _derive_keywords(_src)
            print("\n    search_lineitem queries (no filter, max 15):")
            print("      " + ", ".join(_kw))
            print("\n  Search each by NAME with NO filter, PROVE every candidate with get_actuals")
            print("  for both the printed quarter and the year-ago quarter before it enters the")
            print("  universe, and record the keyword set and the proved ltcodes in kpiDiscovery.")
            print("  At Tier 3 the universe is tagless, so --pick-primary refuses to rank it and")
            print("  `primaryKpiBasis` is required: name the line the issuer's own results")
            print("  statement leads on. Widen with the ltcode spine in [3] if these come back")
            print("  thin.")
    else:
        print("  No keyword row matched. Use the `default` row of the Tier 3 table in")
        print("  kpi-selection-standard.md, and prove every candidate with get_actuals.")

    printed = False
    for heading, cues in SPINES:
        if any(c in low for c in cues):
            block = _slice(text, heading)
            if block:
                print("\n[3] LTCODE-LEVEL SPINE that overrides Revenue/EBITDA/EPS for this sector")
                print("-" * 78)
                print(block)
                printed = True
            break
    if not printed:
        print("\n[3] No sector-specific ltcode spine: the Revenue / EBITDA / EPS spine applies.")
        print("    Capital-intensive and income sectors must still surface capex, FCF or OCF and")
        print("    DPS or payout somewhere in the note (the `relevance` gate blocks a note that")
        print("    carries neither).")

    print("\n[4] REMINDERS THAT APPLY AT EVERY TIER")
    print("  - Prove then include: a KPI enters only if the printed quarter AND the year-ago")
    print("    quarter both return a value. Log any swap in kpiSubstitutions.")
    print("  - `_S_` ltcodes are per-ticker. Never carry one across issuers: re-resolve through")
    print("    search_lineitem for the ticker in front of you.")
    print("  - Carry at least 2 company-specific KPIs, and put the driver KPI in BOTH the")
    print("    Results table and the KPI table.")
    return 0 if row else 3


_KW_STOP = {
    "the","a","an","and","or","of","for","vs","versus","to","in","on","by","with","per",
    "its","their","is","are","be","as","at","from","that","this","it","not","no","new",
    "structural","cyclical","durability","normalization","pressure","risk","growth","mix",
    "margin","margins","rate","rates","case","story","debate","outlook","guide","guidance",
    "issuer","issuers","publishes","publish","actually","often","rather","than","number","many",
    "nothing","which","say","across","reported","mark","asterisk","driver","direction","next-q",
    "next-quarter","lines","frame","off","kpi","kpis",
}
_KW_UNIVERSAL = ("segment revenue", "volume", "price", "backlog", "orders")

def _derive_keywords(row):
    phrases, seen = [], set()
    def add(p):
        p = re.sub(r"`[^`]*`", "", str(p or ""))
        p = re.sub(r"\s*(?:\+|\(|\)| plus | or | - | \u2014 )\s*", ",", p)
        p = re.sub(r"[^A-Za-z0-9&/ ,-]", " ", p)
        for part in re.split(r"\s*(?:/|,|;| and | vs | versus )\s*", p):
            part = " ".join([w for w in part.split()
                             if w.lower() not in _KW_STOP and len(w) > 2][:3])
            part = part.strip().lower()
            if part and part not in seen:
                seen.add(part); phrases.append(part)
    add(row.get("guidance"))
    add(row.get("debate"))
    add(row.get("margin"))
    for u in _KW_UNIVERSAL:
        if u not in seen:
            seen.add(u); phrases.append(u)
    return phrases[:15]


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    arg = sys.argv[1]
    if os.path.exists(arg):
        arg = open(arg, encoding="utf-8").read().strip()
    try:
        rc = brief(arg)
    except BrokenPipeError:
        try:
            sys.stdout.close()
        except Exception:
            pass
        os._exit(0)
    sys.exit(rc)


if __name__ == "__main__":
    main()
