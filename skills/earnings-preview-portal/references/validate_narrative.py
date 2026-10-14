#!/usr/bin/env python3
"""
validate_narrative.py - the gate between Step 4 and Step 5.

  python3 references/validate_narrative.py computed.json narrative.json

WHY THIS EXISTS. Every TABLE cell traces to computed.json; without this gate the prose would be consumed
verbatim, so a fabricated "999,999M", an em-dash, a 280-character bullet or a footer referencing
retired cards would render with zero warnings.

WHAT IT CHECKS
  1. NUMBERS. Every numeric literal in the prose must appear in computed.json at the precision it
     was written to, or as the same value on the $M / bn scale.
  2. STYLE. No em-dashes, no bullet over ~160 rendered characters, no banned adjectives, neutral
     third person.
  3. SCOPE. No reference to a retired card, and none of the retired narrative keys.

WHAT IT DELIBERATELY DOES NOT DO. It does not judge meaning; fiscal labels, bare years, dates,
small integers and ordinals are skipped, because a validator that refuses correct writing teaches
the operator to bypass it.

Exit 0 = the prose may be rendered. Exit 1 = fix the prose. Exit 2 = usage error.
"""
import json
import math
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from jscompat import js_str, js_number, js_parse_float, js_to_fixed, js_round, js_keys, js_cmp_sort, \
    js_stdio, UNDEFINED
from jscompat import _JSErr, _bad_const, _pint, _is_num, _js_json_error, _exit, _out

_A = re.ASCII
_I = re.I | re.ASCII
_WSC = "\t\n\x0b\x0c\r    -     　﻿"
_S = "[" + _WSC + "]"
_TRIM = "\t\n\x0b\x0c\r   " + "".join(chr(c) for c in range(0x2000, 0x200b)) + \
        "    　﻿"
EPSILON = 2.0 ** -52


def _finite(v):
    return _is_num(v) and math.isfinite(v)


def _u16len(s):
    return len(s.encode("utf-16-le", "surrogatepass")) // 2


def _num(v):
    return v if _is_num(v) else js_number(v)


class _NumSet:
    def __init__(self):
        self.d = {}

    @staticmethod
    def _key(v):
        if _is_num(v):
            f = float(v)
            return ("n", "nan") if math.isnan(f) else ("n", f)
        if v is None or v is UNDEFINED:
            return ("z", repr(v))
        if isinstance(v, bool):
            return ("b", v)
        if isinstance(v, str):
            return ("s", v)
        return ("o", id(v))

    def add(self, v):
        k = self._key(v)
        if k not in self.d:
            self.d[k] = (0.0 if (_is_num(v) and v == 0) else v)

    def __iter__(self):
        return iter(list(self.d.values()))

    def __len__(self):
        return len(self.d)


class Error(Exception):
    pass


def _read_json(p):
    if not os.path.exists(p):
        raise Error("ENOENT: no such file or directory, open '%s'" % p)
    if os.path.isdir(p):
        raise Error("EISDIR: illegal operation on a directory, read")
    with open(p, "rb") as f:
        text = f.read().decode("utf-8", "replace")
    try:
        return json.loads(text, parse_constant=_bad_const, parse_int=_pint)
    except (ValueError, RecursionError):
        try:
            _js_json_error(text)
            msg = "Unexpected end of JSON input"
        except _JSErr as e:
            msg = str(e)
    raise SyntaxError(msg)


js_stdio()
_args = sys.argv[1:]
comp_path = _args[0] if len(_args) > 0 else ""
narr_path = _args[1] if len(_args) > 1 else ""
if not comp_path or not narr_path:
    sys.stderr.write("usage: node validate_narrative.js <computed.json> <narrative.json>\n")
    _exit(2)
C = _read_json(comp_path)
N = _read_json(narr_path)

allowed = _NumSet()
b_allowed = _NumSet()
m_allowed = _NumSet()
pct_allowed = _NumSet()
count_allowed = _NumSet()
guide_prior = _NumSet()
TONE_PATH = re.compile(r"^/tone_shift\b", _A)


def add_num(v):
    if not _finite(v):
        return
    allowed.add(v)
    allowed.add(v / 1000)
    allowed.add(v * 1000)
    allowed.add(abs(v))


def _get(o, k):
    if isinstance(o, dict):
        return o.get(k, UNDEFINED)
    if o is None or o is UNDEFINED:
        raise TypeError("Cannot read properties of %s (reading '%s')" % ("null" if o is None else "undefined", k))
    return UNDEFINED


def _entries(o):
    if isinstance(o, dict):
        return [(k, o[k]) for k in js_keys(o)]
    if isinstance(o, (list, str)):
        return [(str(i), x) for i, x in enumerate(o)]
    return []


_cit = _get(C, "citable")
CIT = _cit if (isinstance(_cit, dict) and isinstance(_cit.get("values"), list)) else None
NOT_RENDERED = {"peer_comps", "market_expectations", "irr"}


def walk(n):
    if n is None or n is UNDEFINED or isinstance(n, bool):
        return
    if _is_num(n):
        add_num(n)
        return
    if isinstance(n, str):
        f = js_parse_float(re.sub(r"[^0-9.\-]", "", n))
        if math.isfinite(f):
            add_num(f)
        return
    if isinstance(n, list):
        for x in n:
            walk(x)
        return
    if isinstance(n, dict):
        for k in js_keys(n):
            walk(n[k])


walk({} if CIT is not None else {k: v for k, v in _entries(C) if k not in NOT_RENDERED})


def _vals(v):
    return v if isinstance(v, list) else []


def _s(v):
    if v is UNDEFINED:
        return "undefined"
    if isinstance(v, list):
        return ",".join("" if (x is None or x is UNDEFINED) else _s(x) for x in v)
    if isinstance(v, dict):
        return "[object Object]"
    return js_str(v)


def _abs(v):
    return abs(_num(v))


def _or(a, b):
    return a if (a is not None and a is not UNDEFINED and a is not False and a != "" and
                 not (_is_num(a) and (a == 0 or (isinstance(a, float) and math.isnan(a))))) else b


if CIT is not None:
    for v in CIT["values"]:
        allowed.add(v)
        allowed.add(_abs(v))
    for v in _vals(_or(_get(CIT, "b_ok"), [])):
        b_allowed.add(v)
        b_allowed.add(_abs(v))

    def _b_row(cells):
        nums = [v for v in cells if _is_num(v) and math.isfinite(v)]
        nz = [abs(v) for v in nums if v != 0]
        if nz and 1e3 <= max(nz) < 1e6:
            for v in nums:
                b_allowed.add(v / 1000)
                b_allowed.add(abs(v) / 1000)
            return True
        return False

    for r in _vals(_get(_or(_get(C, "summary_financials"), {}), "rows")):
        if isinstance(r, dict) and r.get("fmt") == "$M":
            _b_row([r.get(k) for k in ("py_q", "prior_q", "target_q", "next_q", "fy_prev", "fy0", "fy1")])
    for r in _vals(_get(_or(_get(C, "revisions"), {}), "rows")):
        if isinstance(r, dict) and r.get("fmt") == "$M" and not r.get("is_pct"):
            d = r.get("delta_vs_post")
            if (_b_row([r.get(k) for k in ("pre", "post", "interq", "latest")]) and r.get("delta_unit") == "abs"
                    and _is_num(d) and math.isfinite(d)):
                b_allowed.add(d / 1000)
                b_allowed.add(abs(d) / 1000)
    for b in _vals(_get(_or(_get(C, "guidance_evolution"), {}), "blocks")):
        if isinstance(b, dict) and not b.get("is_pct") and (b.get("fmt") or "$M") == "$M":
            _b_row([c.get(k) for c in _vals(b.get("cols")) if isinstance(c, dict)
                    for k in ("guide_point", "guide_hi", "guide_lo", "consensus", "actual")])
    def _count_row(cells):
        nums = [v for v in cells if _is_num(v) and math.isfinite(v)]
        nz = [abs(v) for v in nums if v != 0]
        if not nz or max(nz) < 1e6:
            return
        div, tgt = (1e9, b_allowed) if max(nz) >= 1e9 else (1e6, m_allowed)
        for v in nums:
            tgt.add(v / div)
            tgt.add(abs(v) / div)

    for r in _vals(_get(_or(_get(C, "summary_financials"), {}), "rows")):
        if isinstance(r, dict) and r.get("fmt") == "#":
            _count_row([r.get(k) for k in ("py_q", "prior_q", "target_q", "next_q", "fy_prev", "fy0", "fy1")])
    for r in _vals(_get(_or(_get(C, "revisions"), {}), "rows")):
        if isinstance(r, dict) and r.get("fmt") == "#" and not r.get("is_pct"):
            _count_row([r.get(k) for k in ("pre", "post", "interq", "latest")])
    for v in _vals(_or(_get(CIT, "pct"), CIT["values"])):
        pct_allowed.add(v)
        pct_allowed.add(_abs(v))
    for v in _vals(_or(_get(CIT, "counts"), CIT["values"])):
        count_allowed.add(v)
        count_allowed.add(_abs(v))
    for v in _vals(_or(_get(CIT, "guide_prior"), [])):
        guide_prior.add(v)
        guide_prior.add(_abs(v))
else:
    for v in allowed:
        pct_allowed.add(v)
        count_allowed.add(v)

PROSE_KEYS = ["header", "setup_summary", "thesis", "summary_bullets", "setup_bullets",
              "expectations_bullets", "beat_note", "revisions_note", "evolution_note",
              "call_commentary", "tone_shift", "sell_side", "footer"]
RETIRED_KEYS = ["setup_groups", "market_notes", "peer_footnote"]
strings = []


def collect(n, p, bullet):
    if isinstance(n, str):
        strings.append({"path": p, "text": n, "isBullet": bullet})
        return
    if isinstance(n, list):
        for i, x in enumerate(n):
            collect(x, p + "[" + str(i) + "]", True)
        return
    if isinstance(n, dict):
        for k in js_keys(n):
            collect(n[k], p + "/" + k, bullet)


collect({k: _get(N, k) for k in PROSE_KEYS if _get(N, k) is not UNDEFINED}, "", False)

problems = []
advisories = []


def flag(kind, path, detail):
    problems.append({"kind": kind, "path": path, "detail": detail})


PHYS_UNIT = ("(?:GWh?|MWh?|TWh?|kWh|MW|GW|MMBtu|Bcfe?|MMcf|Mcf|MBoe|boe|bbls?|kbd|mbd|kt|koz|Moz|oz|tonnes?|"
             "tons?|lbs?|units?|subscribers?|members?|customers?|accounts?|stores?|locations?|properties|"
             "policies|vehicles?|homes?|wafers?|passengers?|employees?|headcount)")
NUM_RE = re.compile(r"(?<![A-Za-z0-9])-?\d[\d,]*(?:\.\d+)?" + _S + r"*(?:%|bps?|x|B\b|bn\b|billion|M\b|mn\b|million|" +
                    PHYS_UNIT + r"\b)?", _I)
_MON = ("(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|June?|July?|Aug(?:ust)?|"
        "Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)(?![a-z])")
SCRUB = [
    (re.compile(r'"[^"]*"', _A), " "), (re.compile("“[^”]*”", _A), " "),
    (re.compile(r"\b[1-4]Q\d{2}\b", _A), ""), (re.compile(r"\b[12]H\d{2}\b", _A), ""),
    (re.compile(r"\bFY\d{2,4}\b", _A), ""), (re.compile(r"\b\d{4}-\d{2}-\d{2}\b", _A), ""),
    (re.compile(r"\bQ[1-4]\b", _A), ""),
    (re.compile(r"\b52[-" + _WSC + r"]?(?:week|wk)\b", _I), ""),
    (re.compile(r"\b[A-Z]{2}_[A-Z]_\d+(?:-[A-Z]+)?\b", _A), ""),
    (re.compile(r"\badd (?:up )?to 100" + _S + r"?%", _I), ""), (re.compile(r"\bout of 100" + _S + r"?%", _I), ""),
    (re.compile(r"\b100" + _S + r"?% of (?:revenue|the total|total revenue)\b", _I), ""),
    (re.compile(r"\b\d{1,3}[-" + _WSC + r"]*(?:day|session|month|week|quarter|print|broker|analyst)s?\b", _I), ""),
    (re.compile(r"\b\d{1,2}(?:st|nd|rd|th)?" + _S + r"+" + _MON + r"\.?(?:,?" + _S + r"*\d{4})?", _I), ""),
    (re.compile(r"\b" + _MON + r"\.?" + _S + r"+\d{1,2}(?!\d)(?:st|nd|rd|th)?(?:,?" + _S + r"*\d{4})?", _I), ""),
    (re.compile(r"\b\d{1,2}:\d{2}(?::\d{2})?" + _S + r"*(?:AM|PM|ET|EST|EDT|UTC|GMT)?", _I), ""),
]
QUOTE_RE = re.compile("\"[^\"]*\"|“[^”]*”", _A)
QNUM_RE = re.compile(r"-?\d[\d,]*(?:\.\d+)?" + _S + r"*(?:%|bps?|x|B\b|bn\b|billion|M\b|mn\b|million)?", _I)
RAW_RE = re.compile(r"-?\d[\d,]*(?:\.\d+)?", _A)
PHYS_RE = re.compile("^" + PHYS_UNIT + r"\Z", _I)
B_RE = re.compile(r"^(b|bn|billion)\Z", _A)
YEAR_RE = re.compile(r"^\d{4}\Z", _A)


def xlr(n, dp):
    if n is None or n is UNDEFINED:
        return None
    x = _num(n)
    if not math.isfinite(x):
        return x if _is_num(n) else None
    sign = -1 if x < 0 else 1
    a = abs(x)
    f = 10.0 ** dp
    sc = a * f
    return (sign * js_round(sc + abs(sc) * EPSILON * 4)) / f


for s_ in strings:
    path, text = s_["path"], s_["text"]
    scrub = text
    for rx, rep in SCRUB:
        scrub = rx.sub(rep, scrub)
    quoted_nums = []
    for q in QUOTE_RE.finditer(text):
        for t in QNUM_RE.finditer(q.group(0)):
            quoted_nums.append(t.group(0).strip(_TRIM))
    if quoted_nums:
        advisories.append({"path": path, "nums": quoted_nums})
    for m in NUM_RE.finditer(scrub):
        tok = m.group(0)
        rm = RAW_RE.search(tok)
        raw = re.sub(r",+\Z", "", rm.group(0) if rm else "", count=1)
        unit = tok[len(raw):].strip(_TRIM).lower()
        val = js_parse_float(raw.replace(",", ""))
        if not math.isfinite(val):
            continue
        if "." not in raw and "," not in raw and abs(val) <= 12 and not unit:
            continue
        if YEAR_RE.search(raw) and 1900 <= val <= 2100:
            continue
        parts = raw.split(".")
        decs = max(len(parts[1]) if len(parts) > 1 else 0, 1 if unit == "%" else 0)
        xv = xlr(val, decs)

        def eq(st):
            for a in st:
                ya = xlr(a, decs)
                if ya is not None and ya == xv:
                    return True
            return False

        is_b = bool(B_RE.search(unit))
        is_m = unit in ("m", "mn", "million")
        is_pct = unit == "%"
        is_phys = bool(PHYS_RE.search(unit))
        in_tone = bool(TONE_PATH.search(path))
        hit = ((eq(pct_allowed) if is_pct else eq(count_allowed) if is_phys
                else (eq(allowed) or (is_b and eq(b_allowed)) or (is_m and eq(m_allowed))))
               or (in_tone and len(guide_prior) > 0 and eq(guide_prior)))
        near_hint = ""
        if not hit and is_pct:
            near = [a for a in pct_allowed if abs(_abs(a) - abs(val)) < 0.5]
            near = js_cmp_sort(near, lambda a, b: abs(_abs(a) - abs(val)) - abs(_abs(b) - abs(val)))
            if near:
                near_hint = (" The document prints " +
                             " and ".join(dict.fromkeys(js_to_fixed(xlr(a, 1), 1) + "%" for a in near[:3])) +
                             " - cite the printed figure, or put the quantity you mean on the page.")
        if not hit:
            flag("NUMBER", path, '"' + tok.strip(_TRIM) + '" appears in the prose but is not a number ' +
                 ("this document prints AS A PERCENTAGE" if is_pct else "this document prints AS A COUNT" if is_phys
                  else "this document PRINTS") +
                 ((" (checked against the " + _s(_get(CIT, "count")) + "-value citable index" +
                   (", including the billions scale" if is_b else "") + ")") if CIT is not None else "") +
                 ". Every cited number must be in " +
                 "a table, a card or a sub-row first; if the figure is right, put it on the page. If it is " +
                 "management's own words from a parsed transcript, quote them verbatim in quotation marks " +
                 "(listed for review); never quote a data figure to pass this check." + near_hint)

BANNED = re.compile(r"\b(strong|strongly|impressive|robust|notable|notably|remarkable|stellar|solid|healthy|attractive|"
                    r"compelling|significant|dramatic|massive|huge|excellent|outstanding|weak|poor|terrible|"
                    r"disappointing)\b", _I)


def outside_quotes(t):
    t = re.sub(r'"[^"]*"', " ", t)
    t = re.sub("“[^”]*”", " ", t)
    return re.sub(r"'[^']{12,}'", " ", t)


VOICE_RE = re.compile(r"\b(we|our)\b", _I)
BROKER_COUNT = re.compile(r"\b(?:\d+(?:\s*(?:-|–|to)\s*\d+)?|one|two|three|four|five|six|seven|eight|nine|ten|"
                          r"single)[\s-]+(?:brokers?|analysts?)\b|\b(?:broker|analyst)[\s-]*counts?\b|"
                          r"\bnumber of (?:brokers|analysts)\b", _I)
THIN_RE = re.compile(r"\bthin(?:ly)?[\s-]+(?:coverage|covered|marks?|consensus|vintages?|estimates?)\b|"
                     r"\b(?:low|limited|sparse)[\s-]+(?:broker[\s-]+|analyst[\s-]+)?coverage\b|\bone analyst\b|"
                     r"\bsingle[\s-]+(?:broker|analyst)\b", _I)
for s_ in strings:
    path, text, is_bullet = s_["path"], s_["text"], s_["isBullet"]
    if "—" in text:
        flag("EM-DASH", path, "em-dashes are banned in prose; use a comma, " +
             "colon, semicolon or period. En-dash ranges (194–199B) are fine.")
    if is_bullet and _u16len(text) > 160:
        flag("LENGTH", path, "bullet is " + str(_u16len(text)) +
             " characters; the half-width column takes ~160 before it runs past three rendered lines. Split it.")
    unquoted = outside_quotes(text)
    b = BANNED.search(unquoted)
    if b:
        flag("ADJECTIVE", path, '"' + b.group(0) + '" is a banned adjective; use a variance descriptor ' +
             '("above consensus", "compressed") or state the number. (Inside a verbatim quotation it ' +
             "would be allowed - the speaker's word is evidence, not the portal's voice.)")
    if VOICE_RE.search(unquoted):
        flag("VOICE", path, "neutral third person only outside a " +
             "quotation; the portal carries no house view. A verbatim transcript quote in quotation " +
             'marks may say "we" - that is the speaker, not the document.')
    bc = BROKER_COUNT.search(text)
    if bc:
        flag("BROKER-COUNT", path, '"' + bc.group(0) + '": the document never states how many brokers or ' +
             'analysts stand behind a figure. Remove it; cite the consensus figure alone.')
    tc = THIN_RE.search(text)
    if tc:
        flag("COVERAGE", path, '"' + tc.group(0) + '": the document does not comment on how many brokers ' +
             'cover a line - a consensus from one broker is a consensus. Remove the remark.')

for k in RETIRED_KEYS:
    if _get(N, k) is not UNDEFINED:
        flag("RETIRED-KEY", "/" + k, "this key was retired with its card in 2026-07-17 and nothing renders it.")
RETIRED_RE = re.compile(r"peer comps?|peer comparison|market expectations|IRR model|\bIRR\b", _I)
for s_ in strings:
    r = RETIRED_RE.search(s_["text"])
    if r:
        flag("RETIRED-CARD", s_["path"], 'refers to "' + r.group(0) + '", a card that was removed; the reader ' +
             "will look for a section that is not in the document.")


def report_advisories():
    if not advisories:
        return
    n = sum(len(x["nums"]) for x in advisories)
    _out("\n[quoted-figures] " + str(n) + " figure(s) inside quotation marks were NOT checked " +
         "against computed.json - they are evidence from a parsed document, not computed values. " +
         "VERIFY each against the transcript before shipping, and make sure the speaker is named:")
    for a in advisories:
        _out("  " + a["path"] + "  " + ", ".join(a["nums"]))


if not problems:
    _out("NARRATIVE OK - " + str(len(strings)) + " prose string(s) checked against " +
         str(len(allowed)) + " computed values; no fabricated numbers, no style or scope violations.")
    report_advisories()
    _exit(0)
by_kind = {}
for p in problems:
    by_kind.setdefault(p["kind"], []).append(p)
_out("NARRATIVE FAILED - " + str(len(problems)) + " problem(s). Fix the prose, do not render.\n")
for k in by_kind:
    _out("[" + k + "] x" + str(len(by_kind[k])))
    for p in by_kind[k]:
        _out("  " + (p["path"] or "(root)") + "\n      " + p["detail"])
    _out("")
_exit(1)
