"""
polarity.py - THE SINGLE PLACE THAT DECIDES WHICH WAY IS GOOD.

The resolver (which stamps polarity onto a metric set) and compute (which warns when a graded
row rests on an undeclared default) read this one lookup. The code-keyed registry
(assets/polarity_registry.json) outranks the name lexicon, and its label must agree with the
row's name, so a code repurposed for another line never keeps the old direction.

  polarity_of(ltcode, name)       -> 'higher' | 'lower' | 'neutral'
  polarity_declared(ltcode, name) -> did anyone DECIDE, or did it miss every lookup?
  registry_status()               -> {ok, path, count, error}; a failed load is reported, not hidden
"""
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))

POL_LOWER = re.compile("|".join([
    "combined ratio", "loss ratio", "expense ratio", "efficiency ratio", "operating ratio",
    "catastrophe", "accident year", "net npl", "delinquen", "charge[- ]?off", r"\bNCO\b",
    r"\bCASM\b", r"\bAISC\b", r"\bcost\b", r"\bcosts\b", r"\bCOGS\b", r"\bexpense",
    r"\bopex\b", r"\bSG&A\b", "churn", "attrition", "default rate", "cost per",
    r"\bcost of\b", "provision", "impairment", "write[- ]?off", "days (sales|inventory)",
    r"\bburn\b", r"\bburn rate\b", r"\bDSO\b"]), re.I | re.ASCII)
POL_NEUTRAL = re.compile("|".join([
    r"\bcapex\b", "capital expenditure", r"\bdebt\b", r"\bleverage\b", "total assets",
    "total equity", "gross loans", "net loans", r"\bdeposits\b", "acquisition",
    "shares outstanding", r"\bFDS\b", "buyback", "repurchase", "dividend"]), re.I | re.ASCII)

_REG = None
_STATUS = None


def _reg():
    global _REG, _STATUS
    if _REG is not None:
        return _REG
    p = os.path.join(HERE, "..", "assets", "polarity_registry.json")
    try:
        with open(p, encoding="utf-8") as f:
            raw = json.load(f)
        m = (raw or {}).get("metrics") if isinstance(raw, dict) else None
        if not isinstance(m, dict):
            _REG = {}
            _STATUS = {"ok": False, "path": p, "count": 0,
                       "error": "the file parsed but carries no `metrics` object"}
        else:
            _REG = m
            _STATUS = {"ok": True, "path": p, "count": len(m), "error": None}
    except FileNotFoundError:
        _REG = {}
        _STATUS = {"ok": False, "path": p, "count": 0,
                   "error": "ENOENT: no such file or directory, open '%s'" % p}
    except Exception as e:
        _REG = {}
        _STATUS = {"ok": False, "path": p, "count": 0, "error": str(e)}
    return _REG


def registry_status():
    _reg()
    return _STATUS


def _base(lc):
    return re.sub(r"-(Y|Q|M)$", "", str(lc or ""))


def _norm(x):
    return re.sub(r"[^a-z0-9]+", " ", str(x or "").lower().replace("&", " and ")).strip()


def _entry(ltcode, name):
    e = _reg().get(_base(ltcode))
    if not e or not e.get("polarity"):
        return None
    if not name:
        return e
    known = [_norm(x) for x in [e.get("label")] + list(e.get("aliases") or []) if x]
    if not known:
        return e
    return e if _norm(name) in known else None


def polarity_declared(ltcode, name):
    if _entry(ltcode, name):
        return True
    nm = name or ""
    return bool(POL_LOWER.search(nm) or POL_NEUTRAL.search(nm) or re.match(r"^BS_", str(ltcode or "")))


def polarity_of(ltcode, name):
    nm, lc = name or "", ltcode or ""
    e = _entry(lc, nm)
    if e:
        return e["polarity"]
    if POL_LOWER.search(nm):
        return "lower"
    if POL_NEUTRAL.search(nm):
        return "neutral"
    if re.match(r"^BS_", str(lc)):
        return "neutral"
    return "higher"
