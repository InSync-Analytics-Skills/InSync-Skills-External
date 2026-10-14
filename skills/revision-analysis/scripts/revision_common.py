"""
revision_common.py - shared by prepare_revisions.py, build_revision_xlsx.py and
references/sanity_check.py, so the three can never disagree on these settings or formats.
"""
import math
import os
import re

from jscompat import js_round, js_to_fixed

VERSION = "2.5.0"

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


def locale_int(v):
    r = js_round(v)
    if r == 0 and (v < 0 or (v == 0 and math.copysign(1.0, v) < 0)):
        return "-0"
    return format(int(r), ",")


def _trim0(s):
    return re.sub(r"\.?0+$", "", s, count=1)


class MoneyFmt:

    def __init__(self, sym=None):
        sym = sym or "$"
        self.sym = sym
        code = bool(re.match(r"^[A-Z]", sym))
        self.unitHeader = "$M" if sym == "$" else (sym + " M" if code else sym + "M")
        self.deltaHeader = ("Δ$ since print" if sym == "$" else
                            ("Δ " + sym + " since print" if code else "Δ" + sym + " since print"))

    def signed(self, v):
        if v is None:
            return "n/a"
        a, s = abs(v), ("+" if v >= 0 else "−")
        if a >= 1000:
            return s + self.sym + _trim0(js_to_fixed(a / 1000, 2)) + "bn"
        return s + self.sym + locale_int(a) + "m"

    def level(self, v):
        if v is None:
            return "n/a"
        if abs(v) >= 1000:
            return self.sym + _trim0(js_to_fixed(v / 1000, 2)) + "bn"
        return self.sym + locale_int(v) + "m"

    def perShare(self, v):
        if v is None:
            return "n/a"
        from jscompat import js_number
        return self.sym + js_to_fixed(js_number(v), 2)


def money_fmt(sym=None):
    return MoneyFmt(sym)
