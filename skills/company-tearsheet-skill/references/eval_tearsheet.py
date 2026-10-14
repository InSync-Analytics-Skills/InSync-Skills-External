#!/usr/bin/env python3
"""Pre-flight gate: one-page fit limits + full payload schema validation."""
import json
import re
import sys

if len(sys.argv) != 2:
    print("USAGE: eval_tearsheet.py TEARSHEET_DATA.json\n"
          "  Run it on the payload Step 2 wrote; do not hand-assemble one (SKILL.md 12).",
          file=sys.stderr)
    sys.exit(2)
try:
    d = json.load(open(sys.argv[1], encoding="utf-8"))
except OSError as exc:
    print("ERROR: could not read %s: %s" % (sys.argv[1], exc), file=sys.stderr)
    sys.exit(2)
except ValueError as exc:
    print("ERROR: %s is not valid JSON: %s. Re-run Step 2 rather than editing the payload."
          % (sys.argv[1], exc), file=sys.stderr)
    sys.exit(2)
if not isinstance(d, dict):
    print("ERROR: the payload must be a JSON object, got %s" % type(d).__name__,
          file=sys.stderr)
    sys.exit(2)

for _k in ("market_snapshot", "key_financials", "operational_kpis", "valuation_metrics",
           "key_debates", "catalysts", "comparable_companies"):
    _v = d.get(_k)
    if _v is None:
        continue
    if not isinstance(_v, list):
        print("ERROR: payload '%s' must be a JSON array, got %s. Re-run Step 2; do not "
              "hand-assemble the payload." % (_k, type(_v).__name__), file=sys.stderr)
        sys.exit(2)
    _bad = [i for i, r in enumerate(_v) if not isinstance(r, dict)]
    if _bad:
        print("ERROR: payload '%s' must be an array of objects; entries %s are %s. If the "
              "model wrote plain strings here, they belong in the documented shape "
              "(SKILL.md 12) — re-run Step 2."
              % (_k, _bad[:5], type(_v[_bad[0]]).__name__), file=sys.stderr)
        sys.exit(2)
for _k in ("bull_case", "bear_case", "recent_news"):
    _v = d.get(_k)
    if _v is None:
        continue
    if not isinstance(_v, list):
        print("ERROR: payload '%s' must be a JSON ARRAY of strings, got %s. Even a single "
              "bullet is a one-element list (SKILL.md 12). Re-run Step 2." 
              % (_k, type(_v).__name__), file=sys.stderr)
        sys.exit(2)
    _bad = [i for i, x in enumerate(_v) if not isinstance(x, str)]
    if _bad:
        print("ERROR: payload '%s' entries %s are %s, not strings. Re-run Step 2."
              % (_k, _bad[:5], type(_v[_bad[0]]).__name__), file=sys.stderr)
        sys.exit(2)
for _k, _flds in (("key_debates", ("question", "rationale")), ("catalysts", ("date", "event"))):
    for _i, _r in enumerate(d.get(_k) or []):
        for _f in _flds:
            if _f in _r and _r[_f] is not None and not isinstance(_r[_f], str):
                print("ERROR: payload %s[%d]['%s'] must be a string, got %s. Re-run Step 2."
                      % (_k, _i, _f, type(_r[_f]).__name__), file=sys.stderr)
                sys.exit(2)

W = lambda s: len(str(s).split())
fails = []

_qc = d.get("qc")
if isinstance(_qc, dict) and isinstance(_qc.get("failed"), int) \
        and not isinstance(_qc.get("failed"), bool) and _qc["failed"] > 0:
    fails.append(
        "the compute run that produced this payload recorded %s QC FAILURE(S): %s. A failed "
        "QC check means a figure on this sheet is wrong or untrustworthy. Fix raw_data.json "
        "and re-run Step 1 — do not edit the payload to get past this."
        % (_qc["failed"], ", ".join(str(x) for x in (_qc.get("failed_checks") or []))
           or "see run_manifest.json"))
elif _qc is not None and not (isinstance(_qc, dict)
                              and isinstance(_qc.get("failed"), int)
                              and not isinstance(_qc.get("failed"), bool)):
    fails.append("the payload carries a 'qc' key that is not a verdict (%r). It has been "
                 "edited, or did not come from Step 2. Re-run Step 2." % (_qc,))

bb, bc, kd = d.get("bull_case", []), d.get("bear_case", []), d.get("key_debates", [])
rn, cat = d.get("recent_news", []), d.get("catalysts", [])
LIMITS = [
    ("business_overview words", W(d.get("business_overview", "")), 55),
    ("bull_case count", len(bb), 4),
    ("bull_case max words/bullet", max((W(b) for b in bb), default=0), 25),
    ("bear_case count", len(bc), 4),
    ("bear_case max words/bullet", max((W(b) for b in bc), default=0), 25),
    ("key_debates count", len(kd), 3),
    ("debate question max words", max((W(x.get("question", "")) for x in kd), default=0), 15),
    ("debate rationale max words", max((W(x.get("rationale", "")) for x in kd), default=0), 40),
    ("recent_news count", len(rn), 4),
    ("recent_news max words/item", max((W(n) for n in rn), default=0), 20),
    ("catalysts count", len(cat), 4),
    ("key_financials rows (one-page budget)", len(d.get("key_financials", [])), 7),
]
fails += [f"{k}: {v} > limit {lim}" for k, v, lim in LIMITS if v > lim]

FLOORS = [("bull_case", bb, 4), ("bear_case", bc, 4), ("recent_news", rn, 4)]
for _n, _v, _min in FLOORS:
    if not (isinstance(_v, list) and len(_v) >= _min):
        fails.append("%s has %d entries, fewer than the %d SKILL.md 8/11 specify — the "
                     "section renders part-empty. Write the missing ones in prose.json and "
                     "re-run Step 2."
                     % (_n, len(_v) if isinstance(_v, list) else 0, _min))

def require(cond, msg):
    if not cond:
        fails.append(msg)

_EMPTY = (None, "", "-", "–", "—")
PRIMARY = {"market_snapshot": "value", "key_financials": "value",
           "operational_kpis": "value", "valuation_metrics": "current",
           "catalysts": "event"}
_sparse = []

def check_rows(key, fields, min_rows=1, max_rows=None):
    rows = d.get(key)
    require(isinstance(rows, list) and len(rows or []) >= min_rows,
            f"{key}: missing or fewer than {min_rows} rows")
    if max_rows and isinstance(rows, list):
        require(len(rows) <= max_rows, f"{key}: {len(rows)} rows > max {max_rows}")
    for i, r in enumerate(rows or []):
        for f in fields:
            require(isinstance(r, dict) and r.get(f) not in (None, ""),
                    f"{key}[{i}]: missing field '{f}'")
    _pf = PRIMARY.get(key)
    if _pf and isinstance(rows, list) and min_rows >= 1:
        _live = [r for r in rows
                 if isinstance(r, dict) and str(r.get(_pf, "")).strip() not in _EMPTY]
        if len(_live) < min_rows:
            _sparse.append(f"{key}: only {len(_live)} of {len(rows)} rows carry a real "
                           f"'{_pf}' — the rest are the missing-data glyph, so the section "
                           f"reads as {len(rows) - len(_live)} empty slots on the page. The "
                           f"row floor of {min_rows} is met by count, not by content. Either "
                           f"source the data or say in the delivery message that this section "
                           f"is thin.")

for f in ("ticker", "company_name", "prepared_date", "period", "business_overview", "supply_chain",
          "provenance"):
    require(isinstance(d.get(f), str) and d[f].strip(), f"missing required string: '{f}'")

check_rows("market_snapshot", ("label", "value", "context"), 4, 6)
check_rows("key_financials", ("metric", "value", "yoy"), 3, 7)
check_rows("operational_kpis", ("name", "value", "vs_est", "yoy", "trend"), 1, 6)
check_rows("valuation_metrics", ("label", "current", "context"), 3, 6)
check_rows("key_debates", ("question", "rationale"), 3, 3)
check_rows("comparable_companies",
           ("name", "pe", "ev_ebitda", "ev_sales", "pb", "rev_growth"), 2, 6)
check_rows("catalysts", ("date", "event"), 4, 4)

warns = list(_sparse)
_fin = d.get("key_financials") or []
_fin_floor = d.get("min_fin_rows") or 5
if len(_fin) < _fin_floor:
    warns.append(f"key_financials has {len(_fin)} rows; this sector's floor is {_fin_floor} "
                 f"(spec target 7). Check the sector profile and compute_tearsheet.py "
                 f"warnings before shipping.")
if _fin and all(str(r.get("yoy", "")).strip() in ("", "-", "\u2014") for r in _fin):
    warns.append("EVERY key_financials Y/Y cell is blank. The usual cause is a prior-year "
                 "period mismatch: confirm meta.prior_year_quarter and the actuals rows "
                 "(the MCP tags restated periods 'R-2Q25', 'R1-2025' or 'R2-3Q25').")

_prov = str(d.get("provenance") or "")
if "identity:" not in _prov:
    warns.append("IDENTITY NOT RECORDED: the footer does not state how this company was "
                 "resolved, because meta.match_type was not captured (SKILL.md 6.1). Capture "
                 "match_type, score and ambiguous from search_tickerid and re-run compute. "
                 "A similarity match can return a different issuer at status: success, and "
                 "no gate downstream can detect it \u2014 confirm the masthead company, ticker "
                 "and period are the ones you asked for.")
elif not any(t in _prov for t in ("identity: exact_ticker", "identity: exact_company")):
    warns.append("IDENTITY NOT EXACT: the footer records a non-exact match_type. Re-read the "
                 "masthead against the request before delivering, and disclose in the "
                 "response how the company was resolved.")
_nontarget = [r for r in (d.get("comparable_companies") or []) if not r.get("is_target")]
for _v in (d.get("valuation_metrics") or []):
    if not str(_v.get("label", "")).startswith("vs Peer Median"):
        continue
    _m = re.search(r"of (\d+) peers", str(_v.get("context", "")))
    if _m and int(_m.group(1)) > len(_nontarget):
        warns.append("PEER MEDIAN COUNT: '%s' says %s peers but comparable_companies has %d "
                     "non-target row(s). Check that every peer row reached the payload "
                     "(SKILL.md 14 item 12b)." % (_v.get("label"), _m.group(1), len(_nontarget)))
    if str(_v.get("current", "")).strip() in ("+0.0%", "0.0%", "-0.0%"):
        warns.append("PEER MEDIAN +0.0%%: '%s' shows no premium at all, the signature of the "
                     "target compared with itself. Check `is_target` and the peer rows "
                     "(SKILL.md 14 item 12b)." % _v.get("label"))
_kpi = d.get("operational_kpis") or []
if _kpi and all(str(r.get("yoy", "")).strip() in ("", "-", "\u2014") for r in _kpi):
    warns.append("EVERY operational_kpis Y/Y cell is blank — same prior-year period check.")

comps = d.get("comparable_companies") or []
require(sum(1 for r in comps if r.get("is_target")) == 1,
        "comparable_companies: exactly one row must have is_target true")
DASH = {"—", "-", "", None, "unavail"}
for i, r in enumerate(comps):
    if r.get("is_target"):
        continue
    if all(r.get(_f) in DASH for _f in ("pe", "ev_ebitda", "ev_sales", "pb")):
        fails.append("comparable_companies[%d] (%s): no usable multiple — pe, ev_ebitda, "
                     "ev_sales AND pb are all blank. Supply EV/Sales (meaningful with no "
                     "earnings) or P/B (meaningful with no revenue either), or substitute a "
                     "peer that has data."
                     % (i, r.get("name", "?")))
for i, r in enumerate(comps):
    for _f, _tag, _lbl in (("pe", "P", "P/E"), ("ev_ebitda", "E", "EV/EBITDA"),
                           ("ev_sales", "S", "EV/Sales"), ("pb", "B", "P/B")):
        if r.get(_f) in DASH and re.search(_tag + r"\d", str(r.get("rank") or "")):
            fails.append("comparable_companies[%d] (%s): %s is blank but the row still carries "
                         "a %s rank (%s) — a discarded multiple reached the rank and the peer "
                         "median, so the headline vs-Peer-Median figure is wrong"
                         % (i, r.get("name", "?"), _lbl, _lbl, r.get("rank")))

INVERTED_HINT = (r"efficiency ratio", r"combined ratio", r"loss ratio", r"expense ratio",
                 r"\bmlr\b", r"medical loss", r"\bchurn\b", r"\battrition\b",
                 r"\bcosts?\b", r"\bexpenses?\b", r"charge-off", r"delinquen")
_HINT_RE = re.compile("|".join(INVERTED_HINT), re.I)

_NUM = re.compile(r"^\s*(?P<sign>[+\-−])?\s*(?P<cur>[$€£¥]|US\$|HK\$|CHF|SEK|NOK|DKK)?\s*"
                  r"(?P<mant>[\d,]+(?:\.\d+)?)\s*(?P<suf>[KMBT])?\s*(?:%|bps|x)?\s*$")
_MULT = {"": 1.0, "K": 1e3, "M": 1e6, "B": 1e9, "T": 1e12}

VERDICT = re.compile(r"^(Beat|Miss)\s*\(\s*(.+?)\s*(E)?\s*\)\s*$", re.I)


def _scalar(s):
    m = _NUM.match(str(s or ""))
    if not m:
        return None
    g = m.groupdict()
    v = float(g["mant"].replace(",", "")) * _MULT.get(g["suf"] or "", 1.0)
    return -v if g["sign"] in ("-", "−") else v


for i, r in enumerate(d.get("operational_kpis") or []):
    name = str(r.get("name", ""))
    inverted = bool(r.get("lower_is_better"))
    vs_raw = str(r.get("vs_est", ""))
    m = VERDICT.match(vs_raw)
    if m:
        est_txt = m.group(2)
        act, est = _scalar(r.get("value")), _scalar(est_txt)
        if act is None or est is None:
            warns.append("operational_kpis[%d] (%s): verdict %r could not be checked — the "
                         "printed value %r or estimate %r did not parse as a number, so the "
                         "Beat/Miss claim is UNVERIFIED"
                         % (i, name, m.group(1), r.get("value"), est_txt))
        else:
            said_beat = m.group(1).lower() == "beat"
            is_level = bool(m.group(3))
            if is_level:
                should_beat = (act <= est) if inverted else (act >= est)
                detail = "estimate %s" % est_txt
            else:
                should_beat = (est <= 0) if inverted else (est >= 0)
                detail = "delta %s" % est_txt
            if said_beat != should_beat:
                fails.append("operational_kpis[%d] (%s): prints '%s' with actual %s against "
                             "%s%s — the verdict contradicts the printed numbers"
                             % (i, name, m.group(1), r.get("value"), detail,
                                " (lower_is_better)" if inverted else ""))
    if not inverted and _HINT_RE.search(name):
        warns.append("operational_kpis[%d] (%s): the name reads as a lower-is-better metric and "
                     "lower_is_better is NOT set. If lower IS better here, set it in "
                     "raw_data.json or add the ltcode to the sector's lower_is_better_codes "
                     "(assets/sector_profiles.json), or the Beat/Miss verdict and the Y/Y "
                     "colour will both be inverted. If HIGHER is better — cost savings, "
                     "expense reduction, a recovery rate — this warning is expected and the "
                     "row is correct as it stands." % (i, name))

_LTCODE = re.compile(r"^(?:IS|BS|CF|RT|SD|KM|VL|ES)_[PS]_\d+(?:-[A-Z]\d?)?$")
for i, r in enumerate(d.get("operational_kpis") or []):
    if _LTCODE.match(str(r.get("name", "")).strip()):
        fails.append("operational_kpis[%d]: name %r is a raw LTCODE, not a readable label — "
                     "supply kpi_universe from a live search_lineitem(filter='top_kpi') call "
                     "so names come from the MCP (SKILL.md 6.3)" % (i, r.get("name")))

def _dp(s):
    m = _NUM.match(str(s or ""))
    if not m:
        return None
    frac = m.group("mant").split(".")
    return len(frac[1]) if len(frac) > 1 else 0

for _key, _fields in (("operational_kpis", ("value", "vs_est", "yoy")),
                      ("key_financials", ("value", "yoy")),
                      ("market_snapshot", ("value",))):
    for _f in _fields:
        _pcts = [(r.get(_f), _dp(r.get(_f))) for r in (d.get(_key) or [])
                 if str(r.get(_f, "")).strip().endswith("%")]
        _seen = {dp for _v, dp in _pcts if dp is not None}
        if len(_seen) > 1:
            fails.append("%s.%s mixes decimal places %s within one column: %s — SKILL.md 9.3 "
                         "requires one precision per column"
                         % (_key, _f, sorted(_seen), ", ".join(str(v) for v, _ in _pcts)))
        _wrong = [v for v, dp in _pcts if dp is not None and dp != 1]
        if _wrong:
            fails.append("%s.%s renders percentages at %s decimal place(s): %s — SKILL.md 9.3 "
                         "requires ONE. Pass the computed values through unchanged."
                         % (_key, _f, sorted({_dp(v) for v in _wrong}),
                            ", ".join(str(v) for v in _wrong[:6])))

ALLOWED_TRENDS = {"Stable", "Accel", "Decel"}
ALLOWED_TIERS = {"T1", "T2", "S"}
for i, r in enumerate(d.get("operational_kpis") or []):
    if r.get("tier") is not None:
        require(r["tier"] in ALLOWED_TIERS,
                "operational_kpis[%d]: tier %r not in %s" % (i, r.get("tier"), sorted(ALLOWED_TIERS)))
require(W(d.get("supply_chain", "")) <= 45, "supply_chain words: %d > limit 45" % W(d.get("supply_chain", "")))
for i, r in enumerate(d.get("operational_kpis") or []):
    require(r.get("trend") in ALLOWED_TRENDS,
            "operational_kpis[%d]: trend %r not in %s" % (i, r.get("trend"), sorted(ALLOWED_TRENDS)))
for i, b in enumerate((bb or []) + (bc or [])):
    require(isinstance(b, str) and b.strip(), f"thesis bullet {i}: empty or non-string")

PROSE = ([d.get("business_overview", ""), d.get("supply_chain", "")] + (bb or []) + (bc or [])
         + [x.get("question", "") + " " + x.get("rationale", "") for x in (kd or [])]
         + (rn or []) + [c.get("event", "") for c in (cat or [])])
_GROUP = (r"(?:group|peer group|peers|peer set|comp set|comps|cohort|comparables|"
          r"peer universe|the set)")
_SUP_WORD = (r"(?:lowest|highest|cheapest|richest|best|worst|strongest|weakest|fastest|"
             r"slowest|best-positioned|worst-positioned"
             r"|(?<!-)(?:top|bottom)(?=\s+(?:\d+\s+)?(?:rank|ranked|ranking|quartile|"
             r"decile|performer|performers|of\s+the)\b)"
             r"|only(?!\s+(?:\d|about\s+\d|around\s+\d|a\s+(?:third|quarter|half)|"
             r"\$|[\d.]+%)))")
SUPERLATIVE = re.compile(
    r"\b" + _SUP_WORD + r"\b"
    r"(?:[^.!?]|\.\d){0,40}\b(?:in|of|among|amongst|across|versus|vs\.?|against|"
    r"relative to|compared to|compared with|out of)\s+(?:the\s+|its\s+|our\s+)?"
    + _GROUP + r"\b", re.I)
RECOMMEND_HARD = re.compile(
    r"\bwe\s+(?:recommend|advise|reiterate\s+our|would\s+(?:buy|sell|short|avoid|add))\b"
    r"|\b(?:recommend|advise)\s+(?:buying|selling|shorting|avoiding|accumulating)\b"
    r"|\bour\s+(?:recommendation|price\s+target|rating)\b"
    r"|\b(?:initiate|reiterate|upgrade|downgrade)\s+(?:at|to)\s+(?:buy|sell|hold|"
    r"overweight|underweight|neutral)\b", re.I)
_NOT_IMPERATIVE = re.compile(
    r"^\s*(?:short\s+(?:interest|position|positions|base|book|sales?|squeeze|rate|"
    r"duration|term|dated|cycle)"
    r"|buy\s*now\s*pay\s*later|buy[-\s]?side|buy[-\s]?back|buybacks?|buyers?|buyout"
    r"|sell[-\s]?side|sell[-\s]?through|sell[-\s]?in|sellers?"
    r"|add[-\s]?on|adds?\b|added\b|additions?|additional"
    r"|trim(?:med|ming)?\s+(?:the\s+)?(?:book|portfolio|position)"
    r"|avoidance)\b", re.I)
RECOMMEND_SOFT = re.compile(
    r"^\s*(?:buy|sell|short|avoid|accumulate|trim|add)\b", re.I)
BANNED = ["engine intact", "rare pullback", "no room for error", "momentum fading", "flawless execution",
          "compounding machine", "best-in-class", "juggernaut", "skyrocket", "massive opportunity",
          "go long", "go short", "must-own", "screaming buy"]
for i, t in enumerate(PROSE):
    tl = str(t).lower()
    if "\u2014" in repr(t) or "—" in str(t):
        fails.append("style: em dash in prose field %d (use commas, colons, or periods): %r" % (i, str(t)[:60]))
    for b in BANNED:
        if b in tl:
            fails.append("style: banned phrase '%s' in prose field %d" % (b, i))
    _rec = RECOMMEND_HARD.search(str(t))
    if _rec:
        fails.append("style: recommendation language %r in prose field %d — SKILL.md 11 "
                     "forbids first-person recommendations and buy/sell calls. State the "
                     "evidence and let the reader conclude."
                     % (_rec.group(0).strip(), i))
    elif RECOMMEND_SOFT.match(str(t)) and not _NOT_IMPERATIVE.match(str(t)):
        warns.append("style: prose field %d opens with %r, which reads as a buy/sell "
                     "imperative (SKILL.md 11 forbids those). If it is a noun phrase — "
                     "short interest, buy-side flow, add-on acquisitions — this is expected "
                     "and the text is fine; otherwise rewrite it as evidence."
                     % (i, str(t).split()[0] if str(t).split() else ""))
    m = SUPERLATIVE.search(str(t))
    if m:
        fails.append("unsupported: cross-peer superlative %r in prose field %d — computed.json "
                     "carries no peer ranking for this claim; state the figure instead"
                     % (m.group(0)[:50], i))
for i, r in enumerate(d.get("operational_kpis") or []):
    y = str(r.get("yoy", ""))
    inverted = bool(r.get("lower_is_better"))
    if y.startswith("+"):
        favourable = not inverted
    elif y.startswith("-"):
        favourable = inverted
    else:
        favourable = None
    if favourable is False and r.get("trend") == "Accel":
        fails.append("operational_kpis[%d] (%s): trend 'Accel' contradicts YoY %s%s. For an "
                     "inverted metric a RISING value is a deterioration, so 'Accel' claims "
                     "the opposite of what the figure beside it shows (SKILL.md 12.2)"
                     % (i, r.get("name", "?"), y, " with lower_is_better set" if inverted else ""))
    if favourable is True and r.get("trend") == "Decel":
        warns.append("operational_kpis[%d] (%s): trend 'Decel' beside a favourable YoY "
                          "%s. Legitimate if the IMPROVEMENT is slowing; wrong if you meant "
                          "the metric deteriorated. Check it against the prior period."
                          % (i, r.get("name", "?"), y))

_fin = len(d.get("key_financials") or [])
_kpi = len(d.get("operational_kpis") or [])
_peer = len(d.get("comparable_companies") or [])
_val = len(d.get("valuation_metrics") or [])


def _prose_words(dd):
    n = len(str(dd.get("business_overview", "")).split()) \
        + len(str(dd.get("supply_chain", "")).split())
    for k in ("bull_case", "bear_case", "recent_news"):
        n += sum(len(str(x).split()) for x in (dd.get(k) or []))
    for x in (dd.get("key_debates") or []):
        n += len(str(x.get("question", "")).split()) + len(str(x.get("rationale", "")).split())
    for x in (dd.get("catalysts") or []):
        n += len(str(x.get("event", "")).split())
    return n


def _n(count, noun):
    return "%d %s%s" % (count, noun, "" if count == 1 else "s")


_layout = ("LAYOUT: %s, %s, %s, %s, %s. Every per-field cap above is satisfied, which does "
           "NOT guarantee one page — the row counts and the prose compete for the same "
           "space. Step 5 renders the document and decides. If it reports two pages, these "
           "five numbers are the levers; reduce whichever you can most afford."
           % (_n(_fin, "financial row"), _n(_kpi, "KPI row"), _n(_peer, "peer row"),
              _n(_val, "valuation slot"), _n(_prose_words(d), "prose word")))

print("   " + _layout)
if warns:
    print("EVAL ADVISORIES (not blocking, but read them before delivering):")
    [print(f"   ! {m}") for m in warns]
if fails:
    print("EVAL FAILED:")
    [print(f"   {m}") for m in fails]
    sys.exit(1)
print("EVAL PASSED - one-page limits and payload schema all valid")
