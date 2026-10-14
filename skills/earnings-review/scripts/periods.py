"""Fiscal-period labels by MEANING, never by list position (audit class 2).
Quarterly 'nQyy', half-year 'nHyy', nine-month '9Myy', annual 'yyyy' (also 'FYyy' / 'FYyyyy' on input).
Revision prefixes 'R-' / 'Rn-' are stripped; the highest revision wins where both exist (see revision_of).
Drop-in until the shared insync_foundation module is available; keep the API (norm_period, prior_year_of,
prior_period_of, cadence_of, seq_label, sort_key) when swapping."""
import re

_R = re.compile(r"^R(\d*)-")
_Q = re.compile(r"^([1-4])Q(\d{2})$"); _H = re.compile(r"^([12])H(\d{2})$"); _M9 = re.compile(r"^9M(\d{2})$")
_Y = re.compile(r"^(?:FY|CY)?(\d{4})$"); _FY2 = re.compile(r"^FY(\d{2})$")

def norm_period(p):
    s = _R.sub("", str(p or "").strip())
    m = _FY2.match(s)
    return f"20{m.group(1)}" if m else s

def revision_of(p):
    m = _R.match(str(p or "").strip())
    return 0 if not m else int(m.group(1) or 1)

def parse(p):
    s = norm_period(p)
    m = _Q.match(s)
    if m: return ("Q", 2000 + int(m.group(2)), int(m.group(1)))
    m = _H.match(s)
    if m: return ("H", 2000 + int(m.group(2)), int(m.group(1)))
    m = _M9.match(s)
    if m: return ("M9", 2000 + int(m.group(1)), 9)
    m = _Y.match(s)
    if m: return ("Y", int(m.group(1)), 0)
    return None

def label(kind, y, n):
    if kind == "Q": return f"{n}Q{y % 100:02d}"
    if kind == "H": return f"{n}H{y % 100:02d}"
    if kind == "M9": return f"9M{y % 100:02d}"
    return f"{y}"

def prior_year_of(p):
    t = parse(p)
    return None if t is None else label(t[0], t[1] - 1, t[2])

def next_year_of(p):
    t = parse(p)
    return None if t is None else label(t[0], t[1] + 1, t[2])

def prior_period_of(p):
    t = parse(p)
    if t is None: return None
    k, y, n = t
    if k == "Q": return label("Q", y - 1, 4) if n == 1 else label("Q", y, n - 1)
    if k == "H": return label("H", y - 1, 2) if n == 1 else label("H", y, 1)
    if k == "Y": return label("Y", y - 1, 0)
    return None

def next_period_of(p):
    t = parse(p)
    if t is None: return None
    k, y, n = t
    if k == "Q": return label("Q", y + 1, 1) if n == 4 else label("Q", y, n + 1)
    if k == "H": return label("H", y + 1, 1) if n == 2 else label("H", y, 2)
    if k == "Y": return label("Y", y + 1, 0)
    return None

def cadence_of(labels):
    return {t[0] for t in (parse(p) for p in labels) if t}

def seq_label(kind):
    return {"Q": "Q/Q", "H": "H/H"}.get(kind)

def sort_key(p):
    t = parse(p)
    if t is None: return (0, 0)
    k, y, n = t
    end_month = {"Q": 3 * n, "H": 6 * n, "M9": 9, "Y": 12}[k]
    return (y, end_month)

def is_later(a, b):
    return sort_key(a) > sort_key(b)

def fy_parts(kind, y4):
    if kind == "Q": return [label("Q", y4, n) for n in range(1, 5)]
    if kind == "H": return [label("H", y4, n) for n in (1, 2)]
    return [label("Y", y4, 0)]

def contiguous(labels):
    return all(prior_period_of(b) == a for a, b in zip(labels, labels[1:]))
