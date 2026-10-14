"""Number, unit and axis formatting. All display maths lives here.

Rule: the engine never invents a figure. It only scales and formats values that
the caller supplied. The one derived quantity it will compute — and only when
explicitly asked via `derive` — is a growth rate or a ratio between two supplied
series, and that computation is done here, in code, deterministically.
"""
from __future__ import annotations

import math
from typing import Iterable, List, Optional, Sequence

from matplotlib.ticker import FuncFormatter

Number = Optional[float]

UNITS = {
    "usd":      {"label": "US$",        "div": 1.0,   "dp": 0, "pre": "$",  "suf": "", "mag": 1.0,  "base": "US$"},
    "usd_th":   {"label": "US$ th",     "div": 1.0,   "dp": 0, "pre": "$",  "suf": "", "mag": 1e3,  "base": "US$"},
    "usd_mn":   {"label": "US$ mn",     "div": 1.0,   "dp": 0, "pre": "$",  "suf": "", "mag": 1e6,  "base": "US$"},
    "usd_bn":   {"label": "US$ bn",     "div": 1.0,   "dp": 1, "pre": "$",  "suf": "", "mag": 1e9,  "base": "US$"},
    "usd_ps":   {"label": "US$ / share", "div": 1.0,  "dp": 2, "pre": "$",  "suf": "", "mag": 1.0,  "base": "US$ / share"},
    "eur_mn":   {"label": "EUR mn",     "div": 1.0,   "dp": 0, "pre": "€", "suf": "", "mag": 1e6,  "base": "EUR"},
    "num":      {"label": "",           "div": 1.0,   "dp": 1, "pre": "",   "suf": "", "mag": 1.0,  "base": "", "cmin": 1e6},
    "num_mn":   {"label": "mn",         "div": 1.0,   "dp": 1, "pre": "",   "suf": "", "mag": 1e6,  "base": ""},
    "pct":      {"label": "%",          "div": 1.0,   "dp": 1, "pre": "",   "suf": "%", "mag": 1.0, "base": "%"},
    "pct_pt":   {"label": "ppt",        "div": 1.0,   "dp": 0, "pre": "",   "suf": "ppt", "mag": 1.0, "base": "ppt"},
    "bps":      {"label": "bps",        "div": 1.0,   "dp": 0, "pre": "",   "suf": "bps", "mag": 1.0, "base": "bps"},
    "x":        {"label": "x",          "div": 1.0,   "dp": 1, "pre": "",   "suf": "x", "mag": 1.0, "base": "x"},
    "days":     {"label": "days",       "div": 1.0,   "dp": 0, "pre": "",   "suf": "", "mag": 1.0,  "base": "days"},
    "index":    {"label": "Index",      "div": 1.0,   "dp": 0, "pre": "",   "suf": "", "mag": 1.0,  "base": "Index"},
    "boepd":    {"label": "mboe/d",     "div": 1.0,   "dp": 0, "pre": "",   "suf": "", "mag": 1.0,  "base": "mboe/d"},
}
for _code, _pre in (("eur", "€"), ("gbp", "£"), ("jpy", "¥"), ("chf", "CHF "),
                    ("hkd", "HK$"), ("inr", "INR ")):
    _cc = _code.upper()
    UNITS.setdefault(f"{_code}_mn", {"label": f"{_cc} mn", "div": 1.0, "dp": 0, "pre": _pre, "suf": "", "mag": 1e6, "base": _cc})
    UNITS.setdefault(f"{_code}_bn", {"label": f"{_cc} bn", "div": 1.0, "dp": 1, "pre": _pre, "suf": "", "mag": 1e9, "base": _cc})
    UNITS.setdefault(f"{_code}_ps", {"label": f"{_cc} / share", "div": 1.0, "dp": 2, "pre": _pre, "suf": "", "mag": 1.0, "base": f"{_cc} / share"})
del _code, _pre, _cc
NA_TEXT = "n/a"
_SUFFIXES = ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K"))


def unit_meta(unit: Optional[str]) -> dict:
    return dict(UNITS.get(unit or "num", UNITS["num"]))


def _compact_parts(x: float, m: dict):
    mag = m.get("mag", 1.0)
    a = abs(x) * mag
    if a < m.get("cmin", 1e3):
        return None, ""
    for cut, suf in _SUFFIXES:
        if a >= cut:
            return f"{a / cut:,.1f}", suf
    return None, ""


def is_missing(v) -> bool:
    if v is None:
        return True
    if isinstance(v, str):
        return v.strip() == "" or v.strip().lower() in {"na", "n/a", "nm", "n/m", "-", "--", "null", "none"}
    try:
        return math.isnan(float(v))
    except (TypeError, ValueError):
        return True


_MAGNITUDE = {"K": 1e3, "M": 1e6, "MM": 1e6, "B": 1e9, "BN": 1e9, "T": 1e12}
_STRIP = "$€£¥₹,%  "


def to_float(v) -> Number:
    if is_missing(v):
        return None
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v) if math.isfinite(v) else None
    if not isinstance(v, str):
        return None

    s = v.strip()
    negative = s.startswith("(") and s.endswith(")")
    if negative:
        s = s[1:-1]
    for ch in _STRIP:
        s = s.replace(ch, "")
    if not s:
        return None

    mult = 1.0
    tail = s[-2:].upper()
    if tail in _MAGNITUDE and len(s) > 2:
        mult, s = _MAGNITUDE[tail], s[:-2]
    else:
        tail = s[-1:].upper()
        if tail in _MAGNITUDE and len(s) > 1:
            mult, s = _MAGNITUDE[tail], s[:-1]

    try:
        out = float(s) * mult
    except ValueError:
        return None
    if not math.isfinite(out):
        return None
    return -out if negative else out


def magnitude_of(v) -> float:
    if not isinstance(v, str):
        return 1.0
    s = v.strip().strip("()")
    for ch in _STRIP:
        s = s.replace(ch, "")
    for tail in (s[-2:].upper(), s[-1:].upper()):
        if tail in _MAGNITUDE and len(s) > len(tail):
            try:
                float(s[:-len(tail)])
            except ValueError:
                continue
            return _MAGNITUDE[tail]
    return 1.0


def clean(values: Iterable) -> List[Number]:
    return [to_float(v) for v in values]


def invalid_values(values: Iterable) -> List[str]:
    return [repr(v) for v in values if not is_missing(v) and to_float(v) is None]


def _group(n: float, dp: int) -> str:
    return f"{n:,.{dp}f}"


def _auto_dp(v: float, dp: int, unit: Optional[str] = None,
             for_axis: bool = False) -> int:
    a = abs(v)
    if unit in ("num", "num_mn") and float(v).is_integer():
        return 0
    if for_axis:
        return dp
    if dp is not None and dp >= 0:
        if a and a < 1 and dp == 0:
            return 2
        if a and a < 10 and dp == 0:
            return 1
    return dp


def fmt_value(v: Number, unit: Optional[str] = None, dp: Optional[int] = None,
              compact: bool = False, signed: bool = False) -> str:
    if v is None:
        return NA_TEXT
    m = unit_meta(unit)
    d = m["dp"] if dp is None else dp
    d = _auto_dp(v, d, unit)
    x = v / m["div"]
    if compact:
        return _compact(x, m, signed, d)
    sign = "+" if signed and x > 0 else ("−" if x < 0 else "")
    body = _group(abs(x), d)
    return f"{sign}{m['pre']}{body}{m['suf']}"


def _compact(x: float, m: dict, signed: bool, dp: Optional[int] = None) -> str:
    sign = "+" if signed and x > 0 else ("−" if x < 0 else "")
    body, suf = _compact_parts(x, m)
    if body is not None:
        return f"{sign}{m['pre']}{body}{suf}{m['suf']}"
    d = m["dp"] if dp is None else dp
    return f"{sign}{m['pre']}{abs(x):,.{d}f}{m['suf']}"


def axis_scale(peak: Optional[float], m: dict):
    if peak is None:
        return None
    a = abs(peak) * m.get("mag", 1.0)
    if a < m.get("cmin", 1e3):
        return None
    for cut, suf in _SUFFIXES:
        if a >= cut:
            return cut, suf
    return None


def axis_formatter(unit: Optional[str], compact: bool = True, dp: Optional[int] = None,
                   peak: Optional[float] = None):
    m = unit_meta(unit)
    scale = axis_scale(peak, m) if compact else None

    def _f(x, _pos):
        if x is None:
            return ""
        v = x / m["div"]
        a = abs(v)
        if scale is not None:
            if v == 0:
                return f"{m['pre']}0"
            cut, suf = scale
            t = f"{abs(v) * m.get('mag', 1.0) / cut:,.1f}".rstrip("0").rstrip(".")
            return f"{'−' if v < 0 else ''}{m['pre']}{t}{suf}"
        if compact:
            body, suf = _compact_parts(v, m)
            if body is not None:
                s = body.rstrip("0").rstrip(".")
                return f"{'−' if v < 0 else ''}{m['pre']}{s}{suf}"
        d = m["dp"] if dp is None else dp
        d = _auto_dp(v, d, unit, for_axis=True)
        if 0 < a < 1 and d == 0:
            d = 2
        s = f"{abs(v):,.{d}f}"
        neg = "−" if v < 0 and float(s.replace(",", "")) != 0 else ""
        return (f"{neg}{m['pre']}{s}{m['suf']}" if m["suf"] in ("%", "x")
                else f"{neg}{m['pre']}{s}")

    ff = FuncFormatter(_f)
    ff._insync = {"unit": unit, "compact": compact, "dp": dp}
    return ff


def axis_label(unit: Optional[str], explicit: Optional[str] = None,
               peak: Optional[float] = None) -> Optional[str]:
    if explicit is not None:
        return explicit or None
    m = unit_meta(unit)
    if peak is not None and _compact_parts(peak, m)[0] is not None:
        return m.get("base", m["label"]) or None
    return m["label"] or None


def yoy(values: Sequence[Number], periods_per_year: int = 4) -> List[Number]:
    out: List[Number] = []
    for i, v in enumerate(values):
        prior = values[i - periods_per_year] if i >= periods_per_year else None
        if v is None or prior is None or prior <= 0:
            out.append(None)
        else:
            out.append((v / prior - 1.0) * 100.0)
    return out


def qoq(values: Sequence[Number]) -> List[Number]:
    return yoy(values, periods_per_year=1)


def margin(numerator: Sequence[Number], denominator: Sequence[Number]) -> List[Number]:
    out: List[Number] = []
    for n, d in zip(numerator, denominator):
        out.append(None if (n is None or d in (None, 0)) else (n / d) * 100.0)
    return out


def surprise_pct(actual: Sequence[Number], estimate: Sequence[Number]) -> List[Number]:
    out: List[Number] = []
    for a, e in zip(actual, estimate):
        out.append(None if (a is None or e in (None, 0)) else (a / abs(e) - 1.0) * 100.0)
    return out


def rebase(values: Sequence[Number], base: float = 100.0) -> List[Number]:
    first = next((v for v in values if v is not None and v != 0), None)
    if first is None:
        return list(values)
    return [None if v is None else v / first * base for v in values]


def mean(values: Sequence[Number]) -> Number:
    xs = [v for v in values if v is not None]
    return sum(xs) / len(xs) if xs else None


def median(values: Sequence[Number]) -> Number:
    xs = sorted(v for v in values if v is not None)
    if not xs:
        return None
    n = len(xs)
    return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2.0


def percentile(values: Sequence[Number], p: float) -> Number:
    xs = sorted(v for v in values if v is not None)
    if not xs:
        return None
    if len(xs) == 1:
        return xs[0]
    k = (len(xs) - 1) * max(0.0, min(100.0, p)) / 100.0
    lo, hi = math.floor(k), math.ceil(k)
    if lo == hi:
        return xs[int(k)]
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def stdev(values: Sequence[Number]) -> Number:
    xs = [v for v in values if v is not None]
    if len(xs) < 2:
        return None
    mu = sum(xs) / len(xs)
    return math.sqrt(sum((x - mu) ** 2 for x in xs) / (len(xs) - 1))


DERIVATIONS = {
    "yoy": yoy, "qoq": qoq, "margin": margin, "surprise_pct": surprise_pct,
    "rebase": rebase,
}
