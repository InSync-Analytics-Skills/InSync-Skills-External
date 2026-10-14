"""Shared parsing and plumbing for the Earnings Review scripts. Parsing only (text -> number in the metric's unit);
no analytical arithmetic. Every parser KEEPS the currency it read and returns None (never a guess) for a value it
cannot read; callers turn None-from-non-empty-text into a named "could not be read" warning (audit classes 3, 29)."""
import json, re, sys, traceback

_CCY_SYMS = [("GBPence", "GBP", 0.01), ("GBp", "GBP", 0.01), ("US$", "USD", 1.0), ("A$", "AUD", 1.0), ("C$", "CAD", 1.0), ("R$", "BRL", 1.0),
             ("HK$", "HKD", 1.0), ("NZ$", "NZD", 1.0), ("S$", "SGD", 1.0), ("$", "USD", 1.0), ("€", "EUR", 1.0), ("£", "GBP", 1.0), ("¥", "JPY", 1.0), ("₹", "INR", 1.0)]
_SYM = {s: (c, f) for s, c, f in _CCY_SYMS}
_SYM_RE = "|".join(re.escape(s) for s, _, _ in sorted(_CCY_SYMS, key=lambda x: -len(x[0])))
_ISO = r"[A-Z]{3}(?![A-Za-z])"
DISPLAY = {"USD": "$", "EUR": "€", "GBP": "£", "JPY": "¥", "CHF": "CHF ", "AUD": "A$", "CAD": "C$", "BRL": "R$", "INR": "₹", "HKD": "HK$", "NZD": "NZ$", "SGD": "S$"}
def ccy_symbol(code): return DISPLAY.get(code or "USD", f"{code} ")

SUFS = {"T": 1e12, "B": 1e9, "M": 1e6, "k": 1e3, "K": 1e3}
CORE = re.compile(rf"^\(?\s*(?P<s1>-)?\s*(?P<ccy>{_SYM_RE}|{_ISO})?\s*\(?\s*(?P<s2>-)?\s*(?P<num>\d[\d,]*\.?\d*|\.\d+)\s*(?P<suf>bps|bp|[kKMBT%x])?\s*\)?\s*(?P<ccy2>{_ISO})?$")
NUM = CORE
_BLANK = {"", "-", "—", "–", "none", "null"}
_NM = {"n.m.", "nm", "n/m", "na", "n/a", "n.a.", "nmf"}

def classify(s):
    if s is None: return "blank"
    t = str(s).strip()
    if t.lower() in _BLANK: return "blank"
    if t.lower() in _NM: return "nm"
    return "ok" if _match(t) else "unreadable"

def _match(t):
    t = str(t).strip().replace("−", "-")
    t = re.sub(r"\)\s*(bps|bp|[kKMBT%x])$", r"\1)", t)
    m = CORE.match(t)
    if not m: m = CORE.match(re.sub(r"\s+", "", t))
    return m

def _parts(s):
    if s is None: return None
    t = str(s).strip().replace("−", "-")
    m = _match(t)
    if not m: return None
    num = float(m.group("num").replace(",", ""))
    neg = bool(m.group("s1") or m.group("s2") or re.search(r"\(.*\)", t))
    if neg: num = -abs(num)
    tok = m.group("ccy") or m.group("ccy2")
    ccy, sub = (None, 1.0)
    if tok: ccy, sub = _SYM.get(tok, (tok, 1.0))
    return num, (m.group("suf") or ""), ccy, sub

def _vendor_to_unit(num, suf, sub, unit):
    if suf in ("bps", "bp"): return num / 100.0 if unit == "pct" else num
    if unit in ("pct", "num") or suf in ("%", "x"): return num * (sub if suf not in ("%", "x") else 1.0)
    raw = num * SUFS.get(suf, 1.0) * sub
    if unit == "m": return round(raw / 1e6, 9)
    if unit == "b": return raw / 1e9
    if unit == "count": return raw
    if unit == "k": return raw / 1e3
    return num

def parse_value(s, unit):
    p = _parts(s)
    return None if p is None else _vendor_to_unit(p[0], p[1], p[3], unit)

def parse_value_ccy(s):
    p = _parts(s)
    if p is None: return (None, None)
    num, suf, ccy, sub = p
    if suf in ("%", "x"): return (num, ccy)
    if suf in ("bps", "bp"): return (num / 100.0, ccy)
    return (round(num * SUFS.get(suf, 1.0) * sub, 10), ccy)

def currency_of(s):
    p = _parts(s)
    return None if p is None else p[2]

def fix_suffix(s, spec):
    ov = (spec or {}).get("suffix_override")
    if not ov or s is None: return s
    t = str(s).strip(); m = re.match(r"^(.*?)([kKMBT])(\)?)$", t)
    return (m.group(1) + ov.get(m.group(2), m.group(2)) + (m.group(3) or "")) if m else t

SCALES = {"units": 1.0, "thousands": 1e3, "millions": 1e6, "billions": 1e9}
_WORDS = [(r"\s*(?:trillion|tn)\b", "T"), (r"\s*(?:billion|bn)\b", "B"), (r"\s*(?:million|mn|mm)\b", "M"), (r"\s*(?:thousand)\b", "k"),
          (r"\s*(?:percent|per cent|percentage points?)\b", "%"), (r"\s*(?:basis points?)\b", "bps")]
_FIG = rf"\(?\s*-?\s*(?:{_SYM_RE}|{_ISO})?\s*\(?\s*-?\s*\d[\d,]*\.?\d*\s*\)?"
class ScaleError(ValueError): pass

def _words(t):
    t = re.sub(r"\b(approximately|about|around|roughly|nearly|over|some|circa)\b", "", str(t), flags=re.I).replace("~", "")
    for pat, suf in _WORDS: t = re.sub(pat, suf, t, flags=re.I)
    return t.strip()

def _doc_to_unit(num, suf, sub, unit, scale, text):
    if suf == "%": return num
    if suf in ("bps", "bp"): return num / 100.0 if unit == "pct" else num
    if suf == "x": return num
    if suf in SUFS:
        if unit in ("pct", "num"): return num * sub
        raw = num * SUFS[suf] * sub
        return {"m": raw / 1e6, "b": raw / 1e9, "k": raw / 1e3, "count": raw}.get(unit, num)
    if unit in ("pct", "num"): return num * sub
    if scale in SCALES:
        raw = num * SCALES[scale] * sub
        return {"m": raw / 1e6, "b": raw / 1e9, "k": raw / 1e3, "count": raw}.get(unit, num)
    if unit == "count" or scale == "display" or abs(num) < 1000: return num * sub
    raise ScaleError(f"'{text}' has no magnitude word or suffix and is >= 1,000 in a '{unit}' metric: add \"scale\": units | thousands | millions | billions")

def parse_doc_value(text, unit, scale=None):
    if text is None: return None
    t = _words(text)
    p = _parts(re.sub(r"\s+", "", t))
    if p is None:
        m = re.search(rf"{_FIG}\s*(?:bps|bp|[kKMBT%x])(?![A-Za-z])|\(?\s*-?\s*(?:{_SYM_RE}|{_ISO})\s*\(?\s*-?\s*\d[\d,]*\.?\d*\s*\)?", t)
        if not m: return None
        p = _parts(re.sub(r"\s+", "", m.group(0)))
        if p is None: return None
    num, suf, ccy, sub = p
    v = _doc_to_unit(num, suf, sub, unit, scale, text)
    return None if v is None else round(v, 9)

_RANGE = re.compile(rf"({_FIG})\s*(bps|bp|[kKMBT%x])?\s*(?:-|to|–|—)\s*({_FIG})\s*(bps|bp|[kKMBT%x])?(?![A-Za-z])")
_BAND = re.compile(r"\b(?:low|mid|high|upper|lower)\s*[-–]?\s*(?=[\d$€£¥(])|\d\s*%?\s*(?:'?s\b|range\b)", re.I)
def parse_doc_range(text, unit, scale=None):
    if text is None: return None
    t = _words(text)
    t = re.sub(r"\bbetween\s+(.+?)\s+and\s+", r"\1 - ", t, flags=re.I)
    m = _RANGE.search(t)
    if m and re.match(r"(?:low|mid|high|upper|lower)\s*$", t[:m.start()].strip().split(" ")[-1] if t[:m.start()].strip() else "", re.I) and "-" in m.group(0) and not re.search(r"\d", m.group(1)[:m.group(1).find("-") if "-" in m.group(1) else 0]):
        m = None
    if not m:
        if _BAND.search(t): return None
        v = parse_doc_value(t, unit, scale)
        return None if v is None else [v, v]
    a, sa, b, sb = m.group(1), m.group(2), m.group(3), m.group(4)
    sa = sa or sb; sb = sb or sa
    lo = parse_doc_value(re.sub(r"\s+", "", a) + (sa or ""), unit, scale); hi = parse_doc_value(re.sub(r"\s+", "", b) + (sb or ""), unit, scale)
    return None if lo is None or hi is None else sorted([round(lo, 9), round(hi, 9)])

def parse_guide(text, unit, _plain=False):
    if text is None: return None
    return parse_doc_range(str(text), unit, scale="display")

GROWTH_WORDS = [
    ("low single to mid single digit", (1, 6)), ("mid single to high single digit", (4, 9)), ("low single to high single digit", (1, 9)), ("low to high single digit", (1, 9)),
    ("low-to-mid single digit", (1, 6)), ("mid-to-high single digit", (4, 9)), ("low-to-mid teens", (10, 16)), ("mid-to-high teens", (14, 19)),
    ("high single digit to low double digit", (7, 13)), ("high single to low double digit", (7, 13)), ("high single digit", (7, 9)), ("mid single digit", (4, 6)),
    ("low single digit", (1, 3)), ("low double digit", (10, 13)), ("mid double digit", (14, 16)), ("high double digit", (17, 19)), ("mid teens", (14, 16)),
    ("high teens", (17, 19)), ("low teens", (10, 13)), ("low twenties", (20, 23)), ("mid twenties", (24, 26)), ("roughly flat", (-1, 1)), ("broadly flat", (-1, 1)), ("flat", (-1, 1)),
]
_NEG = {"decline", "declines", "declining", "down", "lower", "decrease", "decreases", "decreasing", "contraction", "negative", "drop", "fall"}
_FLAT = {"flat", "roughly flat", "broadly flat"}
_ABBR = {"lsd": "low single digit", "msd": "mid single digit", "hsd": "high single digit", "ldd": "low double digit", "mdd": "mid double digit", "hdd": "high double digit"}
def _norm_words(s):
    s = re.sub(r"\b(lsd|msd|hsd|ldd|mdd|hdd)\b", lambda m: _ABBR[m.group(1)], s.lower())
    return re.sub(r"\s+", " ", re.sub(r"[\-–]+", " ", s)).replace("digits", "digit").replace("single digit percent", "single digit").strip()
def parse_growth_words(text):
    if not text: return None
    mf = re.search(r"\b(flat|down|up)\b(?:\s+(\d+(?:\.\d+)?)\s*%)?\s*(?:to|-|–)\s*(?:an?\s+)?\b(flat|down|up)\b(?:\s+(\d+(?:\.\d+)?)\s*%)?", str(text).lower())
    if mf and (mf.group(2) or mf.group(4)):
        def _end(w, n): return 0.0 if w == "flat" else (None if n is None else (float(n) if w == "up" else -float(n)))
        a_, b_ = _end(mf.group(1), mf.group(2)), _end(mf.group(3), mf.group(4))
        if a_ is not None and b_ is not None: return (min(a_, b_), max(a_, b_))
    for raw_cl in re.split(r"[;,.]|\bbut\b|\bwhile\b|\bwith\b", str(text).lower()):
        cl = _norm_words(raw_cl)
        if not cl: continue
        hits = []
        for ph, (lo, hi) in sorted(GROWTH_WORDS, key=lambda x: -len(x[0])):
            for m in re.finditer(rf"\b{re.escape(_norm_words(ph))}\b", cl):
                if not any(m.start() < e and s_ < m.end() for s_, e, *_ in hits): hits.append((m.start(), m.end(), ph, lo, hi))
        if hits:
            hits.sort(); own = []
            for j, (s_, e_, ph, lo, hi) in enumerate(hits):
                bef = cl[(hits[j - 1][1] if j else 0):s_].split()[-3:]; aft = cl[e_:(hits[j + 1][0] if j + 1 < len(hits) else len(cl))].split()[:2]
                own.append(True if any(w in _NEG for w in bef + aft) else None)
            for j in range(len(hits)):
                if own[j] is None: own[j] = next((own[k] for k in list(range(j - 1, -1, -1)) + list(range(j + 1, len(hits))) if own[k] is not None), False)
            rngs = [(h[3], h[4]) if (h[2] in _FLAT or not own[j]) else (-h[4], -h[3]) for j, h in enumerate(hits)]
            return (min(r[0] for r in rngs), max(r[1] for r in rngs))
        m = re.search(r"(-?\d+(?:\.\d+)?)\s*%?\s*(?:to|-|–)\s*(-?\d+(?:\.\d+)?)\s*%", raw_cl)
        if m:
            lo, hi = float(m.group(1)), float(m.group(2))
            neg = any(w in _NEG for w in raw_cl[:m.start()].split()[-3:])
            return (-hi, -lo) if neg else (lo, hi)
    t = _norm_words(str(text))
    neg = any(re.search(rf"\b{w}\b", t) for w in _NEG)
    if re.search(r"\b(growth|grow|higher|up|increase|increases)\b", t): return (0, None) if not neg else (None, 0)
    if neg: return (None, 0)
    return None

def parse_stated_change(text, kind):
    t = str(text).lower()
    neg = bool(re.search(r"\b(decrease|decreased|decline|declined|down|lower|contraction|reduction)\b", t)) or bool(re.search(r"\(\$?[\d.]+\)", t))
    m = re.search(r"(\d[\d,]*\.?\d*)\s*(?:basis points|basis point|bps|bp)\b", t)
    if m: v, u = float(m.group(1).replace(",", "")), "bps"
    else:
        m = re.search(r"(\d[\d,]*\.?\d*)\s*(?:%|percent)", t)
        if m: v, u = float(m.group(1).replace(",", "")), "pct"
        else:
            m = re.search(r"\$\s*(\d[\d,]*\.?\d*)\s*(billion|million|b|m)?\b", t)
            if not m: return None
            v = float(m.group(1).replace(",", "")); u = "abs"
            sc = (m.group(2) or "").lower(); v = v * (1000 if sc in ("billion", "b") else 1) if kind == "m" else v
    return (-v if neg else v, u)

def load_json(path): return json.load(open(path, encoding="utf-8"))

def payload_rows(obj, fname="", issues=None):
    rows, stack = [], [obj]
    while stack:
        x = stack.pop()
        if isinstance(x, dict):
            if issues is not None:
                for mm in (x, x.get("meta") if isinstance(x.get("meta"), dict) else {}):
                    if mm.get("has_more") is True or (isinstance(mm.get("remaining_items"), (int, float)) and mm["remaining_items"] > 0):
                        issues.append(("has_more", fname))
                if str(x.get("status", "")).lower() == "error": issues.append(("error", fname, str(x.get("message") or x.get("error_type") or "")))
                if x.get("notes"): issues.append(("notes", fname, json.dumps(x["notes"])))
            if isinstance(x.get("data"), list): rows += [r for r in x["data"] if isinstance(r, dict)]
            else: stack += [v for k, v in x.items() if k not in ("request", "meta")]
        elif isinstance(x, list): rows += [r for r in x if isinstance(r, dict)]
    return rows

def envelope(step, message, code=1, **extra):
    print(json.dumps({"status": "error", "step": step, "code": code, "message": message, **extra}, ensure_ascii=False))
    print(f"ERROR: {message}", file=sys.stderr)
    sys.stdout.flush(); sys.exit(code)

def crash_envelope(step):
    def hook(t, e, tb):
        traceback.print_exception(t, e, tb, file=sys.stderr)
        print(json.dumps({"status": "error", "step": step, "code": 1, "message": f"{t.__name__}: {e}"}, ensure_ascii=False)); sys.stdout.flush()
        sys.exit(1)
    sys.excepthook = hook

class EnvelopeArgs(__import__("argparse").ArgumentParser):
    step = "arguments"
    def error(self, message): envelope(self.step, f"arguments: {message}")

def guarded(step, fn):
    try: fn()
    except SystemExit: raise
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        envelope(step, f"{type(e).__name__}: {e}")
