"""Data-coverage and corroboration checks.

The defect this exists for: an issuer spent several billion dollars on acquisitions and the report
said "no material acquisitions occurred". The arithmetic was right, every gate was green, and the
allocation bridge reconciled - because the acquisition line was ABSENT, and absent read as zero.

Publication gates test whether a report contradicts ITSELF. They cannot test whether it
contradicts REALITY. These two checks close that gap:

  1. coverage_report()     - which expected line items returned nothing. A concept the plan asked
                             for and did not receive is a hole, and a hole must be visible.
  2. corroborate()         - independent evidence that a "nothing happened" reading is false.
                             Acquisitions leave traces beyond their own cash line: goodwill and
                             intangibles move, and investing outflow exceeds capex. If the primary
                             line says zero while the traces say otherwise, the zero is not trusted.
"""

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from compute_capalloc import pnum as _pnum, fmt_usd as _fmt
from ltcodes import ACQUISITION_LTCODES as _ACQ_CODES

EXPECTED = {
    "revenue":        ["IS_P_101"],
    "EBIT":           ["IS_P_109"],
    "operating cash flow": ["CF_P_108"],
    "capital expenditure": ["CF_P_109"],
    "free cash flow": ["CF_P_110"],
    "dividends":      ["CF_P_104"],
    "buybacks":       ["CF_P_101"],
    "acquisitions":   list(_ACQ_CODES),
    "cash":           ["BS_P_101"],
    "total debt":     ["BS_P_114", "BS_P_108"],
    "net debt":       ["BS_P_115", "BS_P_109"],
    "equity":         ["BS_P_113"],
    "shares":         ["IS_P_125", "IS_P_250"],
    "net income":     ["IS_P_124", "IS_P_123"],
    "EBITDA":         ["IS_P_114", "IS_P_113"],
}

CORROBORATION_INPUTS = {
    "goodwill":    "BS_P_126",
    "intangibles": "BS_P_127",
}

SECTOR_EXEMPT = {
    "BANKS":      {"capital expenditure", "free cash flow", "EBITDA"},
    "INSURANCE":  {"capital expenditure", "free cash flow", "EBITDA"},
    "REIT":       {"EBITDA"},
}

_RESTATED = re.compile(r"^R(\d*)-((?:19|20)\d{2}|[12]H\d{2,4}|[1-4]Q\d{2,4})$", re.I)
_HALF = re.compile(r"^([12])H(\d{2,4})$", re.I)
_QTR = re.compile(r"^([1-4])Q(\d{2,4})$", re.I)


def _yr4(y):
    y = int(y)
    return y + 2000 if y < 100 else y


def _period_key(per):
    p = str(per or "").strip()
    vint = 0
    m = _RESTATED.match(p)
    if m:
        vint = 1 + (int(m.group(1)) if m.group(1) else 0)
        p = m.group(2)
    if re.fullmatch(r"\d{4}", p):
        return (_yr4(p), 0, vint)
    m = _HALF.match(p)
    if m:
        return (_yr4(m.group(2)), int(m.group(1)) * 2, vint)
    m = _QTR.match(p)
    if m:
        return (_yr4(m.group(2)), int(m.group(1)), vint)
    return (10 ** 6, 0, vint)


def _unparseable(pers):
    return sorted({str(p) for p in pers if _period_key(p)[0] == 10 ** 6})


def _codes_present(rows):
    return {r.get("LTCODE") for r in rows if r.get("LTCODE")}


from compute_capalloc import quarterly_depth


def coverage_report(rows, engine=None):
    have = _codes_present(rows)
    exempt = SECTOR_EXEMPT.get((engine or "").upper(), set())
    missing, present = [], []
    for concept, codes in EXPECTED.items():
        hit = [c for c in codes if c in have]
        (present if hit else missing).append(
            {"concept": concept, "codes": codes, "found": hit})
    missing = [m for m in missing if m["concept"] not in exempt]
    return {
        "requested_concepts": len(EXPECTED),
        "covered": len(present),
        "missing": missing,
        "coverage_pct": round(100.0 * len(present) / max(1, len(EXPECTED)), 1),
        "note": ("%d of %d expected line items returned no data: %s"
                 % (len(missing), len(EXPECTED), ", ".join(m["concept"] for m in missing))
                 if missing else "All expected line items returned data."),
    }


def _ordered(s):
    return sorted(s, key=_period_key)


def corroborate(rows):
    def series(code):
        out = {}
        for r in rows:
            if not isinstance(r, dict) or r.get("LTCODE") != code:
                continue
            v = _pnum(r.get("VALUE"))
            if v is not None:
                out[str(r.get("FISCALPERIOD"))] = v
        return out

    problems = []
    acq = {}
    for c in EXPECTED["acquisitions"]:
        acq.update(series(c))
    acq_total = sum(abs(v) for v in acq.values())

    _explained_floor = 0.5

    def _step_up(code):
        s = series(code)
        if len(s) < 2:
            return None
        ks = _ordered(s)
        first, last = s[ks[0]], s[ks[-1]]
        if not first or (last - first) <= 0:
            return None
        return (first, last, last - first, ks[0], ks[-1])

    if acq_total > 0:
        for code, label, thresh in (("BS_P_126", "goodwill", 0.10),
                                    ("BS_P_127", "intangible assets", 0.25)):
            su = _step_up(code)
            if not su:
                continue
            first, last, rise, k0, k1 = su
            if rise / abs(first) <= thresh:
                continue
            if acq_total >= _explained_floor * rise:
                continue
            problems.append(
                "reported acquisition consideration of %s over the period does not account for "
                "%s rising %.0f%% (%s to %s, an increase of %s): the unexplained %s is the size "
                "of a transaction settled outside the cash line, which the allocation bridge "
                "cannot capture"
                % (_fmt(acq_total, ""), label, 100.0 * rise / abs(first), k0, k1,
                   _fmt(rise, ""), _fmt(rise - acq_total, "")))
        return problems

    for code, label, thresh in (("BS_P_126", "goodwill", 0.10),
                                ("BS_P_127", "intangible assets", 0.25)):
        s = series(code)
        if len(s) >= 2:
            ks = _ordered(s)
            first, last = s[ks[0]], s[ks[-1]]
            if first and (last - first) / abs(first) > thresh:
                problems.append(
                    "no acquisition line is reported, but %s rose %.0f%% over the period "
                    "(%s to %s), which a business combination would explain"
                    % (label, 100.0 * (last - first) / abs(first), ks[0], ks[-1]))
        _bad = _unparseable(s)
        if _bad:
            problems.append(
                "%s carries period tokens this gate cannot order (%s), so the direction of the "
                "movement is unknown and the absent acquisition line is not corroborated"
                % (label, ", ".join(_bad)))
    return problems


def corroboration_basis(rows):
    checked, unavailable = [], []
    for code, label in ((CORROBORATION_INPUTS["goodwill"], "goodwill"),
                        (CORROBORATION_INPUTS["intangibles"], "intangible assets")):
        n = len({str(r.get("FISCALPERIOD")) for r in rows
                 if isinstance(r, dict) and r.get("LTCODE") == code
                 and _pnum(r.get("VALUE")) is not None})
        (checked if n >= 2 else unavailable).append(label)
    return {"checked": checked, "unavailable": unavailable,
            "complete": bool(checked) and not unavailable,
            "note": ("both balance-sheet traces were available and checked"
                     if not unavailable else
                     "%s returned no usable series, so %s of the two balance-sheet traces could "
                     "be checked" % (" and ".join(unavailable),
                                     "neither" if not checked else "only one"))}


def absence_trace_sentence(basis):
    basis = basis or {}
    checked = basis.get("checked") or []
    unavailable = basis.get("unavailable") or []
    if basis.get("complete"):
        return ("Both balance-sheet traces - goodwill and intangible assets - were checked and "
                "neither moved.")
    if checked:
        return ("Of the two balance-sheet traces, %s was available and did not move; %s carried "
                "no data." % (" and ".join(checked),
                              " and ".join(unavailable).capitalize() if unavailable else "the other"))
    return "Neither balance-sheet trace returned data, so the absence is not corroborated."
