#!/usr/bin/env python3
"""Refresh assets/valuation_catalogue.json from get_valuation's own validation error.

get_valuation rejects an unknown metric by listing EVERY valid one, which makes the live
catalogue discoverable in a single call. Usage:

    get_valuation(ticker_id=<any>, mode="NTM", metrics="__CATALOGUE_PROBE__", limit=1)
    python references/valuation_catalogue.py <<< '<the error message text>'
"""
import json, os, re, sys, datetime

def parse_catalogue(err_text: str):
    out = set()
    for line in err_text.splitlines():
        m = re.match(r"\s*-\s*[^:]+:\s*(.+)$", line)
        if m:
            out |= {p.strip() for p in m.group(1).split(",") if p.strip()}
    return sorted(out)

if __name__ == "__main__":
    text = sys.stdin.read()
    metrics = parse_catalogue(text)
    if len(metrics) < 10:
        sys.exit(f"parsed only {len(metrics)} metric(s) - is this the validation error text?")
    p = os.path.join(os.path.dirname(__file__), "..", "assets", "valuation_catalogue.json")
    doc = json.load(open(p))
    before = set(doc.get("metrics", []))
    doc["metrics"] = metrics
    doc["fetched_at"] = datetime.date.today().isoformat()
    json.dump(doc, open(p, "w"), indent=2)
    added = sorted(set(metrics) - before)
    print(f"catalogue: {len(metrics)} metrics"
          + (f" | NEW: {', '.join(added)}" if added else " | no change"))
