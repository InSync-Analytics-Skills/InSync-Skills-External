#!/usr/bin/env python3
"""
sector_resolve.py - fills the `sectorProfile` block that sector_gate() requires.

WHY THIS IS CODE AND NOT PROSE. kpi-selection-standard.md already told the model to prove the
margin ltcode with get_actuals and to read the guidance variable from get_guidance FIRST. Prose
instructions get followed unevenly across chats, which is the same reasoning as
`kpi_gate.py --pick-primary` for the Primary KPI. The model fetches; this
script decides. Same inputs, same profile, every time.

USAGE
  1. Fetch, and save each payload as JSON:
       search_tickerid                       -> insync_subindustry
       get_actuals  ltcode=<ladder>          -> which margin codes actually return
       get_guidance latest=true              -> what the issuer actually guides
  2. Resolve:
       python references/sector_resolve.py subindustry.txt actuals.json guidance.json
     or import and call resolve() directly. Prints the sectorProfile block to stdout.

THE THREE RULES IT ENCODES
  * The sector row is a FALLBACK. Rows come from common/sectors.json, first match wins,
    `default` when nothing matches (which WARNs rather than blocking).
  * The margin must be PROVED. The ladder is walked in order and the first code that returned a
    value wins; every skipped code is logged as a substitution with its reason.
  * Guidance is READ, not assumed. If get_guidance returned rows, the variable comes from the
    metrics the issuer actually guides, ordered by how often it re-guides them. The table row is
    used only when the call came back empty. Real shapes include an issuer that guides no
    revenue line, one that guides neither revenue nor EPS, and one that guides nothing at all.
"""
import json, os, re, sys, collections

HERE = os.path.dirname(os.path.abspath(__file__))
SECTORS = os.path.join(HERE, "..", "common", "sectors.json")

LADDER = ["IS_P_114-M", "IS_P_110-M", "IS_P_106-M", "IS_P_109-M", "IS_P_105-M", "IS_P_113-M",
          "IS_P_118-M", "IS_P_117-M"]
REPORTED = {"IS_P_109-M", "IS_P_105-M", "IS_P_113-M", "IS_P_117-M"}

_ROW_LT = re.compile(r"`([A-Z]{2}_[PS]_[0-9]+(?:-M)?)`")


TWIN = {"IS_P_106-M": "IS_P_105-M", "IS_P_105-M": "IS_P_106-M",
        "IS_P_110-M": "IS_P_109-M", "IS_P_109-M": "IS_P_110-M",
        "IS_P_114-M": "IS_P_113-M", "IS_P_113-M": "IS_P_114-M",
        "IS_P_118-M": "IS_P_117-M", "IS_P_117-M": "IS_P_118-M"}


def row_ladder(row):
    codes = []
    if row:
        for c in _ROW_LT.findall(row.get("margin") or ""):
            codes.append(c)
            twin = TWIN.get(c)
            if twin and twin not in codes:
                codes.append(twin)
    return codes + [c for c in LADDER if c not in codes]


def load_sectors(path=None):
    with open(path or SECTORS, encoding="utf-8") as fh:
        return json.load(fh)


def match_row(subindustry, doc=None):
    doc = doc or load_sectors()
    s = (subindustry or "").lower()
    for r in doc["rows"]:
        if any(m.lower() in s for m in r["match"]):
            return r
    return None


_GUIDE_NOISE = re.compile(r"^\s*(?:Y/Y|Q/Q|% Revenue)\s*-|\bETR\b|\btax|share count|\bFDS\b|"
                         r"diluted shares|interest|amorti[sz]ation|\bSBC\b|stock.based|"
                         r"depreciation|\bFX\b|exchange rate|[A-Z]{3}/[A-Z]{3}", re.I)
_GUIDE_CORE = "revenue sales eps ebit ebitda income margin ffo"
_WORD = re.compile(r"[a-z]{2,}")


def _guidance_variable(guidance_rows, sector_text=None):
    if not guidance_rows:
        return None, []
    freq = collections.Counter()
    label, order = {}, {}
    for r in guidance_rows:
        lt = r.get("LTCODE")
        if not lt:
            continue
        freq[lt] += 1
        label.setdefault(lt, (r.get("LINEITEM") or lt).strip())
        order.setdefault(lt, len(order))
    want = set(_WORD.findall(str(sector_text or _GUIDE_CORE).lower())) - {"next", "per", "and", "the"}

    def key(lt):
        noise = bool(_GUIDE_NOISE.search(label[lt])) or lt.endswith(("-Y", "-Q"))
        hits = len(want & set(_WORD.findall(label[lt].lower())))
        return (noise, -hits, -freq[lt], order[lt])

    ranked = [(label[lt], lt, freq[lt]) for lt in sorted(freq, key=key)]
    return ranked[0][0] if ranked else None, ranked


SHADOWED = {
    "insurance (underwriter)": {
        "probe": "SD_P_103",
        "sibling": "insurance (broker, no `IS_P_403`)",
        "why": "an underwriter reports a combined ratio (SD_P_103); a broker earns commission "
               "and fiduciary income and has none",
    },
    "health care facilities / managed care (payor)": {
        "probe": "IS_P_903",
        "sibling": "health care facilities (hospital / distributor / CRO, no `IS_P_903`)",
        "why": "a payor reports a medical loss ratio (IS_P_903); a hospital, distributor or CRO "
               "has none",
    },
}


def _row_by_id(doc, row_id):
    return next((r for r in doc["rows"] if r["id"] == row_id), None)


def disambiguate(row, returned, doc):
    if not row or row["id"] not in SHADOWED:
        return row, None
    rule = SHADOWED[row["id"]]
    if not returned:
        return row, ("sector row %r shares its match key with %r and get_actuals returned "
                     "nothing to tell them apart, so the first match stands. Re-run "
                     "sector_resolve with the actuals payload: %s."
                     % (row["id"], rule["sibling"], rule["why"]))
    if rule["probe"] in returned:
        return row, ("sector row %r confirmed from the payload: %s returned %r."
                     % (row["id"], rule["probe"], returned[rule["probe"]]))
    sib = _row_by_id(doc, rule["sibling"])
    if sib is None:
        return row, ("sector row %r should hand off to %r, which is not in sectors.json."
                     % (row["id"], rule["sibling"]))
    return sib, ("sector row resolved to %r rather than %r: get_actuals returned no %s, and %s. "
                 "The two rows share the match key %r, so the label alone cannot separate them."
                 % (sib["id"], row["id"], rule["probe"], rule["why"], rule["probe"]))


def resolve(subindustry, actuals_rows, guidance_rows, sectors=None):
    doc = sectors or load_sectors()
    row = match_row(subindustry, doc)

    returned = {}
    for r in actuals_rows or []:
        v = r.get("VALUE")
        if r.get("LTCODE") and v not in (None, "", []):
            returned[r["LTCODE"]] = v

    row, _shadow_note = disambiguate(row, returned, doc)

    _is_rate = lambda v: "%" in str(v)

    margin_lt, subs = None, []
    for code in row_ladder(row):
        if code in returned and _is_rate(returned[code]):
            margin_lt = code
            break
        subs.append({"tried": code, "used": None,
                     "reason": ("get_actuals returned %r, which is a level rather than a rate; "
                                "a margin metric must be a percentage" % returned[code])
                               if code in returned else
                               "get_actuals returned no value for the printed quarter"})
    if margin_lt:
        subs = [dict(s, used=margin_lt) for s in subs]
    else:
        subs = [dict(s, used="NONE") for s in subs]

    gvar, ranked = _guidance_variable(guidance_rows,
                                      (row or {}).get("guidance") or doc["default"]["guidance"])
    live = bool(ranked)

    prof = {
        "subindustry": subindustry,
        "row": row["id"] if row else "default",
        "resolution": "table" if row else "default",
        "marginLtcode": margin_lt,
        "marginProved": bool(margin_lt),
        "guidanceLive": live,
        "guidanceSource": "get_guidance" if live else "table",
        "guidanceVariable": gvar if live else (row["guidance"] if row else doc["default"]["guidance"]),
        "coreDebate": row["debate"] if row else doc["default"]["debate"],
        "substitutions": subs,
    }
    notes = []
    if _shadow_note:
        notes.append(_shadow_note)
    _own = _ROW_LT.findall((row or {}).get("margin") or "")
    if margin_lt and _own and margin_lt == _own[0]:
        notes.append("%s is the margin the %r row prescribes for this sector; it proved out, so "
                     "the generic ladder was not needed." % (margin_lt, prof["row"]))
    if margin_lt in REPORTED:
        notes.append("%s is the REPORTED margin, not ex-1x: mark the row with an asterisk."
                     % margin_lt)
    if not live:
        notes.append("get_guidance returned nothing. Say so in one line in Section 7 and frame "
                     "the outlook off the KPIs; do not invent a guide from the sector row.")
    if not row:
        notes.append("No sector row matched %r. The generic spine is in use and the renderer will "
                     "disclose it." % subindustry)
    if notes:
        prof["notes"] = notes
    return prof


def main():
    if len(sys.argv) < 4:
        print(__doc__); sys.exit(2)
    sub = open(sys.argv[1], encoding="utf-8").read().strip()
    a = json.load(open(sys.argv[2], encoding="utf-8"))
    g = json.load(open(sys.argv[3], encoding="utf-8"))
    a = a.get("data", a) if isinstance(a, dict) else a
    g = (g.get("data", {}) or {}).get("guidance", g) if isinstance(g, dict) else g
    print(json.dumps({"sectorProfile": resolve(sub, a, g)}, indent=1))


if __name__ == "__main__":
    main()
