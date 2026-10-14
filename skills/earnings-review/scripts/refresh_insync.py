"""Earnings Review - refresh_insync.py
Re-applies the InSync consensus layer (insync_layer.py) to an EXISTING data.json without touching hand-filled metadata:
printed-quarter consensus, the consensus probe, next-quarter and full-year snapshots, forward series, KPI universe and
valuation history. Metrics listed in data.json["not_graded"] {id: reason} keep a null consensus (the probe still records
what InSync holds). Use it after new raw pulls, or to move a pre-1.3 workdir to InSync-only consensus.
Usage: python refresh_insync.py <workdir> --fy-period 2026 [--frozen YYYY-MM-DD]
"""
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from insync_layer import build, load_actuals, payload_errors
from erlib import crash_envelope, envelope, EnvelopeArgs
crash_envelope("refresh_insync"); EnvelopeArgs.step = "refresh_insync"

ap = EnvelopeArgs(); ap.add_argument("workdir"); ap.add_argument("--fy-period", required=True); ap.add_argument("--frozen", default="")
a = ap.parse_args(); W = a.workdir; RAW = os.path.join(W, "raw")
if not os.path.exists(os.path.join(W, "data.json")): envelope("refresh_insync", "data.json missing: run build_dataset.py first")
d = json.load(open(os.path.join(W, "data.json"), encoding="utf-8"))
_perr, _foreign = payload_errors(RAW, d["company"]["ticker_id"])
if _foreign: envelope("refresh_insync", f"identity: rows for another issuer (TICKER_ID != {d['company']['ticker_id']}) in {_foreign}", 4)
if _perr: envelope("refresh_insync", "; ".join(_perr))
mm = {k: v for k, v in json.load(open(os.path.join(W, "metric_map.json"), encoding="utf-8")).items() if not k.startswith("_")}
PD = d["period"]; fy_lab = PD["fy_label"]; fy_next = (fy_lab[:-2] + f"{int(fy_lab[-2:]) + 1:02d}") if fy_lab[-2:].isdigit() else fy_lab
frozen = a.frozen or d["consensus"]["frozen_date"]; MISSING = []
_, by = load_actuals(RAW)
L = build(RAW, mm, PD["label"], PD["next_label"], a.fy_period, frozen, fy_lab, fy_next, MISSING, act_by=by)
NG = d.get("not_graded", {}); M = d["metrics"]; changes = []
for mid_ in mm:
    if mid_ not in M: continue
    m = M[mid_]; old = m.get("consensus"); new = L["consensus"].get(mid_)
    if mid_ in NG: new = None
    if (old or {}).get("mean") != (new or {}).get("mean") or (old or {}).get("source") != (new or {}).get("source"):
        changes.append(f"{mid_}: {None if not old else (round(old['mean'], 4), old.get('source'), old.get('count'))} -> {None if not new else (round(new['mean'], 4), new['source'], new['count'])}")
    m["consensus"] = new; m["resolved_code"] = L["resolved"].get(mid_, m.get("resolved_code"))
    if mid_ in L["basis_conflict"]: m["basis_conflict"] = L["basis_conflict"][mid_]
    else: m.pop("basis_conflict", None)
    cn = L["consensus_next"].get(mid_)
    m["consensus_next"] = None if mid_ in NG else cn
    for k in ("consensus_fy", "consensus_fy_next"):
        if mid_ in L[k]: m[k] = L[k][mid_]
        else: m.pop(k, None)
d["forward"] = L["forward"]
d["consensus"].update({"frozen_source": "InSync", "frozen_date": frozen, "cross_check": None})
d["consensus_probe"] = L["probe"]; d["kpi_universe"] = L["kpi_universe"]
d.setdefault("valuation", {})["history"] = L["valuation_history"]
json.dump(d, open(os.path.join(W, "data.json"), "w", encoding="utf-8"), indent=1, ensure_ascii=False)
print(f"refresh_insync: {os.path.basename(os.path.normpath(W))}: {len(changes)} consensus change(s), {len(L['forward'])} forward series, "
      f"KPI universe {L['kpi_universe']['provenance']} ({len(L['kpi_universe']['kpis'])}), valuation history {'yes' if L['valuation_history'] else 'no'}")
for c in changes: print("   ", c)
for x in MISSING: print("   MISSING:", x)
