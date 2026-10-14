"""Earnings Review - apply_overrides.py
Applies <workdir>/overrides.json to data.json: document figures used where InSync has no series, stores only a midpoint,
or holds a different basis. Every entry quotes the document text and names its source; the text is PARSED here (never
typed as a number), the replaced InSync value is logged in data.json["overrides_log"], and build_html discloses the list in
the Source block. Idempotent: run_pipeline.py runs it before compute on every build.

overrides.json
{"values":  [{"metric": "ocf", "period": "2Q26", "text": "$142 million", "source": "2Q26 release, cash flow table", "reason": "InSync not continuing ops"},
             {"metric": "fs", "period": "2Q26", "text": "$ 141,205", "scale": "thousands", "source": "2Q26 release, segment table (in thousands)"}],
             scale: units | thousands | millions | billions -- REQUIRED for a bare figure >= 1,000 in a money metric (the table's own unit line);
             a value 50x away from the InSync value it replaces is refused unless "magnitude_ok": true
 "exclude": [{"metric": "revenue", "periods": ["3Q25", "4Q25", "1Q26"], "reason": "pre-spin basis"}],
 "guides":  [{"metric": "ebitda", "field": "guide_new", "text": "$520 - $560", "source": "2Q26 release outlook"}],
             field: guide_prior | guide_new | guide_fy_prior | guide_fy_new; qualitative text ("Mid-to-high teens growth") is kept as text
 "stated_change": [{"metric": "pe_margin", "text": "an increase of 250 basis points versus the prior year", "basis": "restated", "mode": "replace",
                    "source": "2Q26 call, CFO"}]}
             mode replace   -> the year-ago value is re-based to actual minus the stated change (old-basis InSync comparatives)
             mode alongside -> reported values kept; {id.alt_yago} / {id.alt_yoy} / {id.alt_label} tokens carry the second basis (pro forma)
Usage: python apply_overrides.py <workdir>
"""
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from erlib import parse_doc_value, parse_doc_range, parse_stated_change, envelope, crash_envelope
import periods as PER
crash_envelope("apply_overrides")

W = sys.argv[1] if len(sys.argv) > 1 else "."
po = os.path.join(W, "overrides.json")
if not os.path.exists(po): print("apply_overrides: no overrides.json"); sys.exit(0)
try: O = json.load(open(po, encoding="utf-8"))
except Exception as e: envelope("apply_overrides", f"overrides.json is not valid JSON: {e}")
pd_ = os.path.join(W, "data.json"); d = json.load(open(pd_, encoding="utf-8"))
mm = {}
if os.path.exists(os.path.join(W, "metric_map.json")):
    mm = {k: v for k, v in json.load(open(os.path.join(W, "metric_map.json"), encoding="utf-8")).items() if not k.startswith("_")}
M = d["metrics"]; P = d["periods"]; B = d.get("base_periods") or []
prev = {(x["kind"], x["metric"], x.get("key")): x.get("replaced") for x in d.get("overrides_log", [])}
LOG, ERR = [], []

def unit_of(mid_, o):
    u = o.get("unit") or mm.get(mid_, {}).get("unit")
    if not u: ERR.append(f"{mid_}: no unit (add 'unit' to the override or a metric_map.json entry)")
    return u
def sign_of(mid_): return mm.get(mid_, {}).get("sign", M.get(mid_, {}).get("sign", 1))
def clear_native(mid_, per):
    nat = M[mid_].get("native_yoy")
    if not nat: return
    for p_ in (per, PER.next_year_of(per)):
        if p_ in P: nat[P.index(p_)] = None
def log(kind, mid_, key, value, replaced, o):
    if not o.get("source"): ERR.append(f"{kind} {mid_} {key}: missing 'source'")
    k = (kind, mid_, key)
    LOG.append({"kind": kind, "metric": mid_, "key": key, "value": value, "replaced": prev.get(k, replaced) if k in prev else replaced,
                "text": o.get("text"), "source": o.get("source", ""), "reason": o.get("reason", "")})
def slot(mid_, per):
    m = M[mid_]
    if per in P: return m["values"], P.index(per)
    if per in B:
        if m.get("base") is None: m["base"] = [None] * len(B)
        return m["base"], B.index(per)
    ERR.append(f"{mid_}: period {per} not in periods/base_periods"); return None, None

for o in O.get("values", []):
    mid_ = o["metric"]
    if mid_ not in M: ERR.append(f"values: unknown metric {mid_}"); continue
    u = unit_of(mid_, o)
    try: v = parse_doc_value(o["text"], u, o.get("scale")) if u else None
    except ValueError as e: ERR.append(f"values {mid_} {o.get('period')}: {e}"); continue
    if v is None: ERR.append(f"values {mid_} {o.get('period')}: cannot parse '{o['text']}'"); continue
    if sign_of(mid_) == -1: v = abs(v)
    arr, j = slot(mid_, o["period"])
    if arr is None: continue
    ref = prev.get(("value", mid_, o["period"]), arr[j])
    if ref not in (None, 0) and v not in (0,) and not o.get("magnitude_ok") and (abs(v / ref) > 50 or abs(v / ref) < 1 / 50):
        ERR.append(f"values {mid_} {o['period']}: {v:g} is {abs(v / ref):.3g}x the InSync value {ref:g} (scale?); set the right 'scale' or magnitude_ok: true"); continue
    if ref in (None, 0) and v not in (0,) and not o.get("magnitude_ok"):
        others = [x for k_, x in enumerate(arr) if k_ != j] + [x for x in ((M[mid_].get("base") if arr is M[mid_].get("values") else M[mid_].get("values")) or [])]
        nb = sorted(abs(x) for x in others if isinstance(x, (int, float)) and not isinstance(x, bool) and x)
        med = nb[len(nb) // 2] if nb else None
        if med and (abs(v) / med > 100 or abs(v) / med < 1 / 100):
            ERR.append(f"values {mid_} {o['period']}: {v:g} is {abs(v) / med:.3g}x the median of the metric's other periods ({med:g}); set the right 'scale' or magnitude_ok: true"); continue
    log("value", mid_, o["period"], v, arr[j], o); arr[j] = v; clear_native(mid_, o["period"])

for o in O.get("exclude", []):
    mid_ = o["metric"]
    if mid_ not in M: ERR.append(f"exclude: unknown metric {mid_}"); continue
    for per in o["periods"]:
        arr, j = slot(mid_, per)
        if arr is None: continue
        o2 = dict(o, source=o.get("source") or "basis cut (no value used)")
        log("exclude", mid_, per, None, arr[j], o2); arr[j] = None; clear_native(mid_, per)

FYL = d.get("fy_guide"); FYL = [FYL] if isinstance(FYL, dict) else (FYL or [])
for o in O.get("guides", []):
    mid_, f = o["metric"], o["field"]
    if mid_ not in M: ERR.append(f"guides: unknown metric {mid_}"); continue
    u = unit_of(mid_, o) or "num"
    try: r = parse_doc_range(o["text"], u, o.get("scale"))
    except ValueError as e: ERR.append(f"guides {mid_} {f}: {e}"); continue
    if r: r = sorted(abs(x) if sign_of(mid_) == -1 else x for x in r)
    old = M[mid_].get(f); M[mid_][f] = r
    M[mid_][f + "_text"] = None if r else o["text"]
    if o.get("display"): M[mid_][f + "_text"] = o["display"]
    log("guide", mid_, f, r if r else o["text"], old, o)
    if f.startswith("guide_fy_"):
        side = f.replace("guide_fy_", "")
        row = next((x for x in FYL if x.get("metric") == mid_), None)
        if row is None:
            row = {"label": f"{M[mid_]['label']} ({d['period']['fy_label']})", "fmt": M[mid_]["fmt"], "metric": mid_, "prior": None, "new": None, "ytd_text": "—"}
            FYL.append(row)
        row[side] = r; row[side + "_text"] = None if r else o["text"]
        if o.get("display"): row[side + "_text"] = o["display"]
        if o.get("tag"): row[side + "_tag"] = o["tag"]
        for kk in ("label", "fmt", "ytd_text"):
            if o.get(kk): row[kk] = o[kk]
if FYL: d["fy_guide"] = FYL

for o in O.get("stated_change", []):
    mid_ = o["metric"]
    if mid_ not in M: ERR.append(f"stated_change: unknown metric {mid_}"); continue
    kind = "m" if (mm.get(mid_, {}).get("unit") == "m") else M[mid_].get("kind", "level")
    sc = parse_stated_change(o["text"], kind)
    if sc is None: ERR.append(f"stated_change {mid_}: cannot parse '{o['text']}'"); continue
    if M[mid_].get("kind") == "rate" and sc[1] not in ("bps",): ERR.append(f"stated_change {mid_}: a rate needs a basis-point change, got '{o['text']}'"); continue
    M[mid_]["stated_yoy"] = {"value": sc[0], "unit": sc[1], "basis": o.get("basis", "restated"), "mode": o.get("mode", "replace"),
                             "source": o.get("source", ""), "quote": o["text"]}
    log("stated_change", mid_, o.get("mode", "replace"), sc[0], None, o)

for e in ERR: print("OVERRIDE ERROR:", e, file=sys.stderr)
if ERR: envelope("apply_overrides", f"{len(ERR)} override error(s); data.json not changed. First: {ERR[0]}")
d["overrides_log"] = LOG
json.dump(d, open(pd_, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
print(f"apply_overrides: {len(LOG)} override(s) applied")
