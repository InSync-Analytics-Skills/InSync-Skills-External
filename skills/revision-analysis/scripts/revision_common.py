"""
revision_common.py - shared by prepare_revisions.py, build_revision_xlsx.py and
references/sanity_check.py, so the three can never disagree on these settings or formats.
"""
import math
import os
import re
from decimal import Decimal, ROUND_HALF_UP

from jscompat import js_to_fixed

VERSION = "2.5.2"

STRICT_DEFAULT = True
_env = os.environ.get("REV_STRICT")
STRICT_CHECKS = True if _env == "1" else False if _env == "0" else STRICT_DEFAULT

CUR_RE = re.compile(r"^(\$|€|£|¥|₹|₩|[A-Z]{2,3}\$?)(?=\s*-?[\d.])")


def cur_token(s):
    if s is None:
        return None
    from jscompat import js_str
    m = CUR_RE.search(js_str(s).strip())
    return m.group(1) if m else None


def cur_sym(t):
    return "$" if (t is None or t == "$" or t == "USD" or t == "US$") else t


CUR_PAT = r"(?:\$|€|£|¥|₹|₩|[A-Z]{2,3}\$?)"


def hu_fixed(x, d=0):
    x = float(x)
    if not math.isfinite(x):
        return js_to_fixed(x, d)
    q = Decimal(repr(x)).quantize(Decimal(1).scaleb(-d), rounding=ROUND_HALF_UP)
    return format(abs(q) if q == 0 else q, "f")


def locale_int(v):
    return format(int(Decimal(hu_fixed(v, 0))), ",")


class MoneyFmt:

    def __init__(self, sym=None):
        sym = sym or "$"
        self.sym = sym
        code = bool(re.match(r"^[A-Z]", sym))
        self.unitHeader = "$M" if sym == "$" else (sym + " M" if code else sym + "M")
        self.deltaHeader = ("Δ$ since print" if sym == "$" else
                            ("Δ " + sym + " since print" if code else "Δ" + sym + " since print"))

    def _amt(self, a):
        a = abs(a)
        if a >= 999999.995:
            return self.sym + hu_fixed(a / 1e6, 2) + "T"
        if a >= 999.995:
            return self.sym + hu_fixed(a / 1000, 2) + "B"
        return self.sym + hu_fixed(a, 2) + "M"

    def signed(self, v):
        if v is None:
            return "n/a"
        t = self._amt(v)
        return t if t == self.sym + "0.00M" else ("+" if v >= 0 else "−") + t

    def level(self, v):
        if v is None:
            return "n/a"
        t = self._amt(v)
        return ("−" if v < 0 and t != self.sym + "0.00M" else "") + t

    def perShare(self, v):
        if v is None:
            return "n/a"
        from jscompat import js_number
        t = hu_fixed(js_number(v), 2)
        return ("−" + self.sym + t[1:]) if t.startswith("-") else self.sym + t


def money_fmt(sym=None):
    return MoneyFmt(sym)
