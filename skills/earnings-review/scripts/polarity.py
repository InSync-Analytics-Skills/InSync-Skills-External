"""Polarity: is a higher actual good or bad for a line? (audit classes 13, 13b, 24)
Precedence: metric_map 'better' (validated: higher | lower, case-insensitive) -> assets/polarity_registry.json by LTCODE
(a '-M' / '-Y' variant inherits its base code; a company-specific _S_ code only for the ticker_ids its entry lists) -> UNDECLARED. There is no silent default: an undeclared graded row is a
validator ERROR naming the code, whatever its label looks like.
CLI: python polarity.py --lint   -> every code named in SKILL.md, references/ and templates/ must be in the registry (exit 1 if not)."""
import json, os, re, sys, glob

HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
REG_PATH = os.path.join(ROOT, "assets", "polarity_registry.json")
_REG = None
def registry():
    global _REG
    if _REG is None: _REG = json.load(open(REG_PATH, encoding="utf-8"))["codes"]
    return _REG

def validate_better(v):
    if v is None: return None
    s = str(v).strip().lower()
    if s in ("higher", "lower"): return s
    raise ValueError(f"'better' must be 'higher' or 'lower', got {v!r}")

def _entry(code):
    R = registry()
    if code in R: return R[code]
    return R.get(re.sub(r"-[MYQ]$", "", str(code or "")))

def company_specific(code): return "_S_" in str(code or "")

def lookup(code, ticker_id=None):
    e = _entry(code)
    if e is None or not company_specific(code): return e
    return e if ticker_id is not None and str(ticker_id) in {str(t) for t in e.get("ticker_ids", [])} else None

def resolve(codes, spec_better=None, ticker_id=None):
    b = validate_better(spec_better)
    if b: return b, "metric_map"
    for c in codes:
        e = lookup(c, ticker_id)
        if e is not None:
            if e.get("better"): return e["better"], f"registry {c}"
            return None, f"registry {c}: {e.get('reason', 'direction not declared')}"
    other = [c for c in codes if company_specific(c) and _entry(c) is not None]
    if other: return None, f"no registry entry for {', '.join(codes)} at ticker_id {ticker_id}; the registry's {other[0]} is another issuer's line item (company-specific codes differ by company)"
    return None, f"no registry entry for {', '.join(codes) or 'this line'}"

def declared(codes, spec_better=None, ticker_id=None):
    return resolve(codes, spec_better, ticker_id)[0] is not None

def documented_codes():
    files = [os.path.join(ROOT, "SKILL.md")] + glob.glob(os.path.join(ROOT, "references", "*.md")) + glob.glob(os.path.join(ROOT, "templates", "*.json"))
    out = set()
    for f in files:
        out |= set(re.findall(r"\b(?:IS|SD|CF|KM|BS|RT)_[PS]_\d+(?:-[MYQ])?\b", open(f, encoding="utf-8").read()))
    return out

if __name__ == "__main__":
    if "--lint" in sys.argv:
        miss = sorted(c for c in documented_codes() if _entry(c) is None)
        und = sorted(c for c in documented_codes() if (_entry(c) or {}).get("better") is None and _entry(c) is not None)
        unscoped = sorted(c for c, e in registry().items() if company_specific(c) and not isinstance(e.get("ticker_ids"), list))
        print(f"documented codes: {len(documented_codes())}; registry entries: {len(registry())}; undeclared: {len(miss)}; declared as undetermined: {len(und)}; "
              f"company-specific entries without ticker_ids: {len(unscoped)}")
        for c in miss: print("MISSING", c)
        for c in unscoped: print("UNSCOPED", c, "(a company-specific code needs the ticker_ids it applies to)")
        sys.exit(1 if miss or unscoped else 0)
    print(__doc__)
