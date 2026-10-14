"""Earnings Review - tokens.py: print the computed tokens the prose needs, compactly, instead of reading computed.json.
Usage: python tokens.py <workdir> [prefix ...]      e.g.  python tokens.py ex/SMH_3Q26 revenue. eps. fwd. guide.
No prefix -> one line per metric with its key tokens, then the non-metric namespaces (reaction, val, fwd, guide, fy, d, f)."""
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from erlib import envelope, crash_envelope
crash_envelope("tokens")
if len(sys.argv) < 2: envelope("tokens", "usage: tokens.py <workdir> [prefix ...]")
W = sys.argv[1]; P = sys.argv[2:]
if not os.path.exists(os.path.join(W, "computed.json")): envelope("tokens", "computed.json missing: run compute.py first")
T = json.load(open(os.path.join(W, "computed.json"), encoding="utf-8"))["tokens"]
if P:
    for k, v in T.items():
        if any(k.startswith(p) for p in P): print(f"{{{k}}} = {v}")
    sys.exit(0)
KEY = ("actual", "consensus", "consensus_n", "surprise", "verdict", "yoy", "guide_prior", "guide_pos", "guide_new", "consensus_next")
mids = sorted({k.split(".")[0] for k in T if k.count(".") >= 1 and f"{k.split('.')[0]}.verdict" in T})
for m in mids:
    print(m + ": " + " | ".join(f"{f}={T[f'{m}.{f}']}" for f in KEY if f"{m}.{f}" in T and T[f"{m}.{f}"] not in ("—", "")))
for ns in ("reaction.", "val.", "fwd.", "guide", "fy", "d.", "f.", "i.", "kpi.", "co.", "period", "consensus."):
    ks = [k for k in T if k.startswith(ns) and k.split(".")[0] not in mids]
    if ks: print(ns.rstrip(".") + ": " + " | ".join(f"{k.split('.', 1)[-1] if ('.' in k and k.split('.')[0] == ns.rstrip('.')) else k}={T[k]}" for k in ks))
