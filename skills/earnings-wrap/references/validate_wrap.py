#!/usr/bin/env python3
"""
validate_wrap.py — shipped, executable validation for the earnings-wrap skill.
Runs SIX mandatory layers over the composed HTML before rendering:
  1. house_asserts  — punctuation / brand / placeholder residue / portability guard
  2. format_lint    — sentence / paragraph / bullet caps + bullet-dominance
  3. wrap_eval      — fixed 11-section skeleton, order, table whitelist, lexicon
  4. numbers_lint   — ARITHMETIC: surprise%, verdict band, revision math,
                      segment reconciliation (the anti-fabrication guard)
  5. dedup_lint     — no repeated facts across sections (stock move stated once)
  6. content_lint   — evidence-based writing: movement claims need numbers, no unsupported
                      opinions, monitoring bullets tied to reported figures (content rules)

Usage:  python references/validate_wrap.py <wrap.html>
Exit 0 = all pass; exit 1 = failures printed. Never loosen a threshold to pass.
"""
import re, sys, os, json

def _clean(s):
    return re.sub(r'\s+', ' ', re.sub(r'&[a-z]+;|&#\d+;', ' ',
                  re.sub(r'<[^>]+>', ' ', s))).strip()

def house_asserts(html):
    body = re.sub(r'<style[^>]*>.*?</style>', ' ', html, flags=re.S | re.I)
    _nomark = re.sub(r'<td[^>]*>\s*\u2014\s*</td>', '<td></td>', body, flags=re.I)
    txt = re.sub(r'<[^>]+>', ' ', _nomark)
    prose = re.sub(r'<[^>]+>', ' ', re.sub(r'<th[^>]*>.*?</th>', ' ', body, flags=re.S | re.I))
    iss = []
    if '—' in txt: iss.append("em dash present")
    if '+/-' in txt: iss.append("'+/-' present (use ±)")
    if re.search(r'\bvs\b', prose): iss.append("' vs ' present (use versus)")
    if '{{' in html: iss.append("placeholder residue")
    if 'data:image/png;base64' not in html: iss.append("missing base64 logo")
    if 'Georgia' not in html: iss.append("missing Georgia")
    if 'print-color-adjust:exact' not in html.replace(' ', ''):
        iss.append("missing print-color-adjust:exact (white-masthead portability guard)")
    return iss

def palette_lint(html, css_src=None):
    HOUSE = {"#E8850C", "#B25E08", "#1A1A1A", "#222222", "#6E6E6E", "#555555",
             "#1E7A34", "#B02A1F", "#EDEDED", "#EEEEEE", "#BBBBBB", "#DDDDDD",
             "#F5F0E8", "#F6F3EE", "#F0E9D8", "#C9C9C9", "#FFFFFF"}
    iss = []
    if css_src is not None:
        stray = sorted(set(re.findall(r"#[0-9A-Fa-f]{3,6}", css_src)))
        if stray:
            iss.append("stylesheet writes colour literals %s - use a __TOKEN__ from PALETTE"
                       % ", ".join(stray))
        if "__TOKEN__" in css_src:
            iss.append("stylesheet carries a literal __TOKEN__ placeholder")
    off = sorted({h.upper() for h in re.findall(r"#[0-9A-Fa-f]{6}", html)} - HOUSE)
    if off:
        iss.append("off-palette colour(s) in the rendered wrap: %s" % ", ".join(off))
    for need in ("#F5F0E8", "#EDEDED"):
        if need not in html.upper():
            iss.append("expected house fill %s absent - zebra or header band lost" % need)
    return iss

def format_lint(html):
    x = re.sub(r'<style[^>]*>.*?</style>', ' ', html, flags=re.S | re.I)
    x = re.sub(r'<script[^>]*>.*?</script>', ' ', x, flags=re.S | re.I)
    sents = lambda t: [s for s in re.split(r'(?<=[.!?])\s+', t) if s.strip()]
    iss = []
    def sr(t, w):
        for s in sents(t):
            sn = re.sub(r'(?<=\d),(?=\d)', '', s)
            broke, n, ncom = [], len(s.split()), sn.count(',')
            if n > 30:     broke.append("%d words, limit 30" % n)
            if ';' in s:   broke.append("semicolon, use two sentences")
            if ncom > 1:   broke.append("%d commas, limit 1" % ncom)
            if broke:
                iss.append("%s [%s]: %s" % (w, "; ".join(broke), s[:110]))
    for p in re.findall(r'<p class="b">(.*?)</p>', x, flags=re.S):
        t = _clean(p); sr(t, "p.b")
        if len(sents(t)) > 3 or len(t.split()) > 55:
            iss.append(f"PARA {len(sents(t))}s/{len(t.split())}w: {t[:55]}")
    for li in re.findall(r'<li>(.*?)</li>', x, flags=re.S):
        t = _clean(li); sr(t, "li")
        if len(sents(t)) > 3: iss.append(f"BULLET: {t[:55]}")
    if len(re.findall(r'<ul class="r">', x)) < 5:
        iss.append("TOO FEW bullet lists")
    return iss

# 2026-10-16: consensus is never dated on the page. "Consensus on Sep 29 modelled...", "[E]
# pre-print consensus (as of Jul 14, 2026)", "the post-print mean (Aug 8, 2026)" and a revisions
# window "Jul 27, 2026 pre-print to Aug 29, 2026 post-print" all fail; write pre-print or
# post-print instead. A sentence (or table cell) fails when it names consensus AND carries a day
# and month, so guide dates ("the guide standing after the Apr 14 call") and price dates
# ("Oct 15, 2026 close") still pass.
_CD_MON = (r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|"
           r"sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\.?")
_CD_DATE = re.compile(r"(?i)(?:\b\d{1,2}\s+" + _CD_MON + r"(?![a-z])|\b" + _CD_MON + r"\s+\d{1,2}\b"
                      r"|\b\d{4}-\d{2}-\d{2}\b)")
_CD_CONS = re.compile(r"(?i)(?:\bconsensus\b|\bstreet\b|\bmean\b|\[e\]|\bpre-print\b|\bpost-print\b"
                      r"|\bestimates?\b)")

def consensus_dates(html):
    """Every sentence or table cell on the page that names consensus and carries a date."""
    import html as _h
    x = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", html)
    units = list(re.findall(r"(?is)<t[dh]\b[^>]*>(.*?)</t[dh]>", x))
    prose = re.sub(r"(?is)<table\b.*?</table>", " ", x)
    prose = re.sub(r"(?i)<br\s*/?>|</(p|li|div|h\d)>", " . ", prose)
    units += re.split(r"(?<=[.;])\s+", prose)
    hits = []
    for u in units:
        t = re.sub(r"\s+", " ", _h.unescape(re.sub(r"<[^>]+>", " ", u))).strip()
        if t and _CD_CONS.search(t) and _CD_DATE.search(t):
            hits.append(t[:160])
    return hits

def wrap_eval(html):
    iss = []
    x = re.sub(r'<style[^>]*>.*?</style>', ' ', html, flags=re.S | re.I)
    SECTIONS = ["variant view", "event snapshot", "results versus expectations",
        "what drove the quarter", "kpi hierarchy and performance",
        "guidance and estimate revisions", "management commentary",
        "key monitoring variables", "valuation context"]
    heads = [_clean(h).lower() for h in re.findall(r'<h2 class="sec">(.*?)</h2>', x, flags=re.S)]
    pos = []
    for s in SECTIONS:
        hit = [i for i, h in enumerate(heads) if s in h]
        if not hit: iss.append("MISSING section: " + s)
        else: pos.append(hit[0])
    if pos != sorted(pos): iss.append("SECTIONS OUT OF ORDER")
    for cls in re.findall(r'<table class="([^"]*)"', x):
        if cls not in ("kv", "data", "data rev", "data react", "mast"):
            iss.append("NON-WHITELIST table: " + cls)
    low = _clean(x).lower()
    for w in [r'\battractive\b', r'\bcompelling\b', r'\bundervalued\b', r'\bovervalued\b',
              r'investors should', r'\boverweight\b', r'\bunderweight\b', r'price target',
              r'upside potential', r'\bbuy\b(?!-?side)',
              r'\bsell\b(?!-?side)(?!-(?:through|in|out)\b)']:
        if re.search(w, low): iss.append("BANNED lexicon: " + w)
    for _t in consensus_dates(x):
        iss.append("DATED CONSENSUS (say pre-print or post-print, never a date): " + _t)
    secs = re.split(r'<h2 class="sec">', x)
    def _sec(name):
        for p in secs:
            if name in _clean(p.split("</h2>")[0]).lower():
                return p.lower()
        return ""
    snap = _sec("event snapshot")
    if snap:
        if "(primary kpi)" not in snap:
            iss.append("Event Snapshot missing the (Primary KPI) tag")
        if "(key kpi)" not in snap:
            iss.append("Event Snapshot missing a second (Key KPI) row (e.g. Backlog) -- add the top secondary KPI")
        for col in ("actual", "change"):
            if col not in snap:
                iss.append("Event Snapshot not in the standardized Metric|Actual|Change grid (Rule 9)")
                break
    g7 = _sec("guidance and estimate revisions")
    if g7 and "<table" not in g7:
        iss.append("Guidance section has no table -- add the mandatory Guidance table")
    kpi_tbl = None
    for tb in re.findall(r'<table class="data".*?</table>', x, flags=re.S):
        rows = re.findall(r'<tr[^>]*>(.*?)</tr>', tb, flags=re.S)
        if not rows: continue
        head = [_clean(c) for c in re.findall(r'<th[^>]*>(.*?)</th>', rows[0], flags=re.S)]
        if head[:1] == ["KPI"] and head[-1:] == ["Y/Y"]:
            kpi_tbl = (head, rows); break
    if kpi_tbl is None:
        iss.append("KPI Hierarchy has no deterministic 'KPI ... Y/Y' value+growth table (Rule 10/11)")
    else:
        head, rows = kpi_tbl
        na = {"", "n/a", "-", "—", "na"}
        for tr in rows[1:]:
            cells = [_clean(c) for c in re.findall(r'<td[^>]*>(.*?)</td>', tr, flags=re.S)]
            if len(cells) < 3: continue
            name, printed, yy = cells[0], cells[-2], cells[-1].lower()
            if printed.lower() not in na and yy in na:
                iss.append(f"KPI row shows a value with NO Y/Y (Rule 10/11): {name!r}")
    for tb in re.findall(r'<table class="data[^"]*">.*?</table>', x, flags=re.S):
        cols = re.findall(r'<col style="width:([\d.]+)%"', tb)
        first_tr = re.search(r'<tr[^>]*>(.*?)</tr>', tb, flags=re.S)
        nth = len(re.findall(r'<th', first_tr.group(1))) if first_tr else 0
        if nth and cols:
            if len(cols) != nth:
                iss.append(f"TABLE-WIDTH integrity: {len(cols)} column widths for {nth} header cells (overflow risk)")
            tot = sum(float(c) for c in cols)
            if not (98.0 <= tot <= 102.0):
                iss.append(f"TABLE-WIDTH integrity: column widths sum to {tot:.1f}%% (must be ~100%%)")
    return iss

def _dp(cell):
    m = re.search(r'\d[\d,]*(?:\.(\d+))?', _clean(cell))
    return len(m.group(1)) if m and m.group(1) else 0


def _num(cell):
    s = _clean(cell)
    neg = s.strip().startswith('-') or s.strip().startswith('(')
    m = re.search(r'(\d[\d,]*(?:\.\d+)?)', s)
    if not m: return None
    try:
        v = float(m.group(1).replace(',', ''))
    except ValueError:
        return None
    return -abs(v) if neg else v

def numbers_lint(html):
    iss = []
    x = re.sub(r'<style[^>]*>.*?</style>', ' ', html, flags=re.S | re.I)

    for tbl in re.findall(r'<table class="data react".*?</table>', x, flags=re.S):
        for tr in re.findall(r'<tr[^>]*>(.*?)</tr>', tbl, flags=re.S):
            tds = re.findall(r'<td[^>]*>(.*?)</td>', tr, flags=re.S)
            if len(tds) < 5: continue
            metric = _clean(tds[0]); a = _num(tds[1]); c = _num(tds[2])
            v = _clean(tds[4]).lower()
            if a is None or c is None or 'not graded' in v or 'n/a' in v or c <= 0: continue
            s = (a / c - 1) * 100
            mm = re.search(r'([+-]?\d+(?:\.\d+)?)\s*%', _clean(tds[4]))
            if mm and abs(float(mm.group(1)) - s) > 1.0:
                iss.append(f"SURPRISE {metric}: stated {mm.group(1)}% vs recomputed {s:.1f}%")
            if s > 0.5 and 'beat' not in v and 'above' not in v:
                iss.append(f"VERDICT {metric}: +{s:.1f}% not labeled Beat")
            if s < -0.5 and 'miss' not in v and 'below' not in v:
                iss.append(f"VERDICT {metric}: {s:.1f}% not labeled Miss")
            if abs(s) <= 0.5 and 'in-line' not in v and 'in line' not in v:
                iss.append(f"VERDICT {metric}: {s:.1f}% (in band) not In-line")
            if abs(s) > 40 and 'n/m' not in v and 'below' not in v:
                _hint = ("ex-SBC/one-time; lead the verdict on revenue or operating income"
                         if 'eps' in metric.lower() else
                         "reported-versus-adjusted, or a segment line carrying identified "
                         "items; say which basis each side is on")
                iss.append(f"BASIS: {metric} surprise {s:+.0f}% exceeds 40% -- verify basis "
                           f"({_hint}). A surprise this size is usually definition, not result.")

    for tbl in re.findall(r'<table class="data".*?</table>', x, flags=re.S):
        head = _clean(tbl).lower()
        if 'before [e]' not in head or 'after [e]' not in head: continue
        for tr in re.findall(r'<tr[^>]*>(.*?)</tr>', tbl, flags=re.S):
            tds = re.findall(r'<td[^>]*>(.*?)</td>', tr, flags=re.S)
            if len(tds) < 4: continue
            pre = _num(tds[1]); post = _num(tds[2])
            mm = re.search(r'([+-]?\d+(?:\.\d+)?)\s*%', _clean(tds[3]))
            if pre and post and mm:
                calc = (post / pre - 1) * 100
                hp, hq = 0.5 * 10 ** -_dp(tds[1]), 0.5 * 10 ** -_dp(tds[2])
                lo = hi = calc
                if abs(pre) > hp:
                    ends = [((q / p) - 1) * 100 for p in (pre - hp, pre + hp) for q in (post - hq, post + hq)]
                    lo, hi = min(ends), max(ends)
                if not (lo - 0.2 <= float(mm.group(1)) <= hi + 0.2):
                    iss.append(f"REVISION {_clean(tds[0])}: stated {mm.group(1)}% vs {calc:.1f}%")


    for tbl in re.findall(r'<table class="data".*?</table>', x, flags=re.S):
        heads = [_clean(h).lower() for h in re.findall(r'<th[^>]*>(.*?)</th>', tbl, flags=re.S)]
        if not heads or 'dir.' not in heads: continue
        if 'actual [a]' not in heads or 'consensus [e]' not in heads: continue
        ai, ci = heads.index('actual [a]'), heads.index('consensus [e]')
        _after = x.split(tbl, 1)[-1][:1200]
        _cap_text = _clean(re.search(r'<div class="cap">(.*?)</div>', _after, re.S).group(1)) \
            if re.search(r'<div class="cap">(.*?)</div>', _after, re.S) else ""
        for tr in re.findall(r'<tr[^>]*>(.*?)</tr>', tbl, flags=re.S):
            tds = re.findall(r'<td[^>]*>(.*?)</td>', tr, flags=re.S)
            if len(tds) <= max(ai, ci): continue
            metric = _clean(tds[0])
            at, ct = _clean(tds[ai]), _clean(tds[ci])
            a, c = _num(tds[ai]), _num(tds[ci])
            if a is None or c is None or c == 0: continue
            if '%' in at or 'pp' in at: continue
            if (a < 0) != (c < 0): continue
            s2 = (a / c - 1) * 100
            if abs(s2) <= 40: continue
            if re.search(re.escape(metric.split("(")[0].strip()), _cap_text, re.I): continue
            iss.append(
                f"BASIS: {metric} prints a {s2:+.0f}% surprise against consensus ({at} versus "
                f"{ct}) and the Section 4 caption does not mention it. Above 40% a surprise is "
                f"usually DEFINITION, not result - a reported line graded against an "
                f"adjusted-basis estimate, a segment carrying identified items, or a restated "
                f"base. The caveat has to be on the PAGE, next to the number.")
    return iss

def dedup_lint(html):
    x = re.sub(r"<style[^>]*>.*?</style>", " ", html, flags=re.S | re.I)
    items = [_clean(b).lower() for b in
             re.findall(r"<li>(.*?)</li>", x, flags=re.S) +
             re.findall(r'<p class="b">(.*?)</p>', x, flags=re.S)]
    items = [b for b in items if len(b.split()) >= 4]
    iss = []
    rx = r"\b(shares|the stock)\b.{0,40}\b(rose|fell|climbed|dropped|gained|declined|slipped|jumped|advanced|sank)\b"
    react = [b for b in items if re.search(rx, b)]
    if len(react) > 1:
        iss.append("stock reaction stated " + str(len(react)) + "x (keep once, in Executive Summary): " + react[0][:44])
    seen = {}
    for b in items:
        key = " ".join(b.split()[:6])
        if len(key) >= 22:
            seen[key] = seen.get(key, 0) + 1
    for key, n in seen.items():
        if n > 1:
            iss.append("repeated bullet opening " + str(n) + "x (state once): " + key)
    return iss

_MOVE = re.compile(r"\b(beat|beats|missed|exceeded|expand\w*|contract\w*|improv\w*|"
    r"deteriorat\w*|accelerat\w*|decelerat\w*|slow\w*|surg\w*|plung\w*|jump\w*|rose|fell|"
    r"grew|declin\w*|rising|falling|higher|lower|stronger|weaker|outperform\w*|underperform\w*|"
    r"doubled|tripled|record|reaccelerat\w*)\b", re.I)
_OPINION = ["future optionality","investors believe","the market thinks","market believes",
    "premium valuation","expensive valuation","attractive multiple","cheap valuation",
    "rich valuation","priced for perfection","the market is pricing","premium multiple",
    "expensive multiple","cheap multiple","rich multiple","premium to peers","discount to peers"]

_MGMT_CUE = re.compile(r"\b(guid\w*|said|say|expect\w*|plan\w*|reaffirm\w*|target\w*|"
    r"prioriti\w*|announc\w*|intend\w*|sees|committ?\w*|raised|rais\w*|lower\w*|cut|"
    r"maintain\w*|outlook|strateg\w*|aims?|cited|noted|highlight\w*|reiterat\w*|"
    r"management|ceo|cfo|will|framework|invest\w*|allocat\w*|no (formal )?guide|no .*target)\b", re.I)
_FIG = re.compile(r"(?<![\w.])\$?\d+(?:\.\d+)?\s?(?:bps|pp|%|bn|[BMTkx])(?![\w])")

def content_lint(html):
    x = re.sub(r"<style[^>]*>.*?</style>", " ", html, flags=re.S | re.I)
    iss = []
    for li in re.findall(r"<li>(.*?)</li>", x, flags=re.S):
        t = _clean(li)
        if len(t.split()) < 3: continue
        if _MOVE.search(t) and not re.search(r"\d", t):
            iss.append(f"UNQUANTIFIED claim (Rule 1, add a number): \"{t[:60]}\"")
    low = _clean(x).lower()
    for ph in _OPINION:
        if ph in low:
            iss.append(f"UNSUPPORTED opinion phrase (Rule 2): '{ph}'")
    secs = re.split(r'<h2 class="sec">', x)
    mon = ""
    for p in secs:
        if "key monitoring variables" in _clean(p.split("</h2>")[0]).lower():
            mon = p; break
    for li in re.findall(r"<li>(.*?)</li>", mon, flags=re.S):
        t = _clean(li)
        if len(t.split()) >= 3 and not re.search(r"\d", t):
            iss.append(f"GENERIC monitoring bullet (Rule 4, tie to a number): \"{t[:55]}\"")
    mgmt = ""
    for p in secs:
        if "management commentary" in _clean(p.split("</h2>")[0]).lower():
            mgmt = p.split('<h2 class="sec">')[0]; break
    for li in re.findall(r"<li>(.*?)</li>", mgmt, flags=re.S):
        t = _clean(li)
        if len(t.split()) < 3: continue
        if re.search(r"[$%]|\bbps\b", t) and not _MGMT_CUE.search(t):
            iss.append(f"RESULT-RESTATEMENT in Management Commentary (Rule 3, needs a management statement): \"{t[:60]}\"")
    from collections import Counter
    cnt = Counter()
    for li in re.findall(r"<li>(.*?)</li>", x, flags=re.S):
        t = _clean(li)
        if len(t.split()) < 3: continue
        for tok in set(m.group(0).replace(" ", "") for m in _FIG.finditer(t)):
            cnt[tok] += 1
    for tok, n in cnt.items():
        if n > 3:
            iss.append(f"REPEATED figure across {n} prose bullets (Rule 6, give it one home section): '{tok}'")
    return iss

_CT_STRIP = re.compile(r"\b(total|adj|adjusted|consolidated|reported)\b")
_CT_PLURAL = re.compile(r"(?<=[a-z])s\b")
_CT_NUM = re.compile(r"^\s*([+-]?)\s*\$?£?€?¥?\s*([0-9][0-9,]*\.?[0-9]*)\s*"
                     r"(bn|B|M|K|T|pp|%|x)?\s*$", re.I)

def _ct_canon(label):
    s = re.sub(r"<[^>]+>", " ", label or "")
    s = re.sub(r"&[a-z]+;", " ", s)
    s = re.sub(r"\([^)]*\)", " ", s)
    s = _CT_STRIP.sub(" ", s.lower())
    s = re.sub(r"[^a-z0-9\- ]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return _CT_PLURAL.sub("", s)

def _ct_num(cell):
    t = re.sub(r"<[^>]+>", "", cell or "")
    t = t.replace("&mdash;", "").replace("&nbsp;", " ")
    t = t.replace("↑", "").replace("↓", "").strip()
    m = _CT_NUM.match(t)
    if not m:
        return None
    raw = m.group(2).replace(",", "")
    dp = len(raw.split(".")[1]) if "." in raw else 0
    val = float(raw) * (-1 if m.group(1) == "-" else 1)
    return val, (m.group(3) or "").upper(), dp

_CT_PERIOD = re.compile(r"\[A\]\s*$")

def _ct_num_delta(cell):
    t = str(cell).replace("&#8593;", " ").replace("&#8595;", " ")
    t = t.replace("&uarr;", " ").replace("&darr;", " ")
    return _ct_num(t)

def _ct_show(cell):
    t = re.sub(r"<[^>]+>", "", str(cell))
    for e, r in (("&#8593;", "up "), ("&#8595;", "down "), ("&uarr;", "up "),
                 ("&darr;", "down "), ("&mdash;", "-"), ("&nbsp;", " ")):
        t = t.replace(e, r)
    return re.sub(r"\s+", " ", t).strip()

def _ct_roles(heads):
    txt = [re.sub(r"<[^>]+>", "", h).strip() for h in heads]
    acol = next((i for i, h in enumerate(txt) if re.search(r"^actual", h, re.I)), None)
    if acol is None:
        periods = [i for i, h in enumerate(txt) if _CT_PERIOD.search(h)]
        if len(periods) >= 2:
            acol = periods[-1]
    ycol = next((i for i, h in enumerate(txt)
                 if re.search(r"^(y/y|change \(y/y\))$", h, re.I)), None)
    return acol, ycol

_REACT_ROW = re.compile(r"(?i)stock\s+reaction")

def _react_pct(cell):
    s = re.sub(r"&#\d+;|&[a-z]+;", " ", str(cell))
    m = re.search(r"([+-]?\d+(?:\.\d+)?)\s*%", s)
    if not m:
        return None
    v = float(m.group(1))
    if v > 0 and re.search(r"(?i)\bfell\b|\bdropped\b|\bdeclined\b|\bslid\b|\bdown\b|&#8595;|\u2193", s):
        v = -v
    return v


def reaction_agreement(html):
    issues = []
    tcell = None
    for tbl in re.findall(r"<table[^>]*>(.*?)</table>", html, re.S):
        for r in re.findall(r"<tr[^>]*>(.*?)</tr>", tbl, re.S):
            cells = re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", r, re.S)
            if cells and _REACT_ROW.search(_clean(cells[0])):
                for c in cells[1:]:
                    v = _react_pct(_clean(c))
                    if v is not None:
                        tcell = (_clean(cells[0]), _clean(c), v)
                        break
            if tcell:
                break
        if tcell:
            break
    if tcell is None:
        return issues
    _lbl, _raw, tval = tcell
    prose = [_clean(li) for li in re.findall(r"<li>(.*?)</li>", html, re.S)]
    cands = []
    for li in prose:
        if re.search(r"(?i)\bnet\b\s*:|\bshares?\b|\bstock\b|reaction", li):
            v = _react_pct(li)
            if v is not None:
                cands.append((v, li))
    if not cands:
        issues.append("REACTION FIGURE UNCORROBORATED (advisory): the Event Snapshot prints %s "
                      "for the stock reaction and no prose bullet states it. The MCP has no "
                      "reaction field - it is computed off two closes by hand - so prose is the "
                      "only independent check there is. State it once, in the Market reaction "
                      "'Net:' bullet." % _raw)
        return issues
    if not any(abs(v - tval) <= 0.1 for v, _ in cands):
        near = min(cands, key=lambda c: abs(c[0] - tval))
        issues.append("STOCK REACTION DISAGREES WITH ITS OWN PROSE: the Event Snapshot prints %s "
                      "but the note says %+.1f%% in prose (%r). The reaction is the one table "
                      "figure the renderer cannot compute - it comes off two closes by hand - so "
                      "these two are the only cross-check on it. Recompute from the reaction "
                      "session's close against the prior close and make both say the same thing."
                      % (_raw, near[0], near[1][:70]))
    return issues


def crosstable_lint(html):
    issues = list(reaction_agreement(html))
    seen = {}
    seen_yoy = {}
    for ti, tbl in enumerate(re.findall(r"<table[^>]*>(.*?)</table>", html, re.S)):
        rows = re.findall(r"<tr[^>]*>(.*?)</tr>", tbl, re.S)
        if not rows:
            continue
        heads = re.findall(r"<th[^>]*>(.*?)</th>", rows[0], re.S)
        acol, ycol = _ct_roles(heads)
        if ycol is not None:
            ylocal = {}
            for r in rows[1:]:
                cells = re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", r, re.S)
                if len(cells) <= ycol: continue
                key = _ct_canon(cells[0])
                if not key: continue
                ylocal.setdefault(key, []).append((cells[0], cells[ycol]))
            for key, hits in ylocal.items():
                if len(hits) > 1: continue
                label, cell = hits[0]
                parsed = _ct_num_delta(cell)
                if parsed is None: continue
                if key in seen_yoy:
                    _l, _c, _p = seen_yoy[key]
                    if _p[1] != parsed[1] or round(_p[0], 1) != round(parsed[0], 1):
                        issues.append(
                            "SAME METRIC, TWO Y/Y FIGURES: %r prints %s in one table and %s in "
                            "another. Both are computed from the same reported figures, so a "
                            "disagreement means two different priors were supplied - reconcile "
                            "`results[].py` with the year-ago cell of `kpiTable.vals`."
                            % (key, _ct_show(_c), _ct_show(cell)))
                else:
                    seen_yoy[key] = (label, cell, parsed)
        if acol is None:
            continue
        local = {}
        for r in rows[1:]:
            cells = re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", r, re.S)
            if len(cells) <= acol:
                continue
            key = _ct_canon(cells[0])
            if not key:
                continue
            local.setdefault(key, []).append((cells[0], cells[acol]))
        for key, hits in local.items():
            if len(hits) > 1:
                continue
            label, cell = hits[0]
            parsed = _ct_num(cell)
            if parsed is None:
                continue
            if key in seen:
                _ti, _label, _cell, _p = seen[key]
                if _p[1] != parsed[1]:
                    issues.append("MAGNITUDE MISMATCH: %r prints %s and %s in two tables."
                                  % (key, re.sub(r"<[^>]+>", "", _cell).strip(),
                                     re.sub(r"<[^>]+>", "", cell).strip()))
                else:
                    dp = min(_p[2], parsed[2])
                    if round(_p[0], dp) != round(parsed[0], dp):
                        issues.append(
                            "SAME METRIC, TWO VALUES: %r prints %s and %s in two different "
                            "tables. Give the snapshot row a `ref` to the Results row so both "
                            "cells format the same figure."
                            % (key, re.sub(r"<[^>]+>", "", _cell).strip(),
                               re.sub(r"<[^>]+>", "", cell).strip()))
            else:
                seen[key] = (ti, label, cell, parsed)
    return list(dict.fromkeys(issues))

_DL_DASH = {"—", "–", "-", "", "n/a", "N/A", "na"}
_DL_CELL = re.compile(
    r"^([-+]?)\s*"
    r"(?:\$|EUR|USD|GBP|CHF|JPY|GBp|€|£|¥|₹)?\s*"
    r"([\d,]+(?:\.\d+)?)\s*"
    r"(bn|B|M|T|K|pp|bps|%|x)?\s*$")

def _dl_val(tok):
    t = re.sub(r"<[^>]+>", "", str(tok))
    t = t.replace("&#8593;", "").replace("&#8595;", "").replace("&mdash;", "—")
    t = t.replace("&nbsp;", " ").replace(" ", "").strip()
    if t in _DL_DASH: return None
    m = _DL_CELL.match(t)
    if not m: return None
    digits = m.group(2).replace(",", "")
    n = float(digits)
    if m.group(1) == "-": n = -n
    dec = len(digits.split(".")[1]) if "." in digits else 0
    return (n, (m.group(3) or "").strip(), 0.5 * 10 ** (-dec))

def _dl_rows(html):
    for tb in re.findall(r"<table[^>]*>(.*?)</table>", html, re.S):
        trs = re.findall(r"<tr[^>]*>(.*?)</tr>", tb, re.S)
        if not trs: continue
        heads = [re.sub(r"<[^>]+>", "", h).strip()
                 for h in re.findall(r"<th[^>]*>(.*?)</th>", trs[0], re.S)]
        body = []
        for tr in trs[1:]:
            if re.search(r'<tr[^>]*class="[^"]*band', tr): continue
            tds = re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)
            if len(tds) == len(heads) and len(tds) > 2: body.append(tds)
        if heads and body: yield heads, body

def _dl_col(heads, *pats):
    for i, h in enumerate(heads):
        for p in pats:
            if re.search(p, h, re.I): return i
    return None

_DL_PERIOD = re.compile(r"^\s*(\d)Q(\d\d)\s*\[A\]|^\s*(\d)H(\d\d)\s*\[A\]|^\s*(\d{4})\s*\[A\]")

def _dl_year_ago_label(lbl):
    t = str(lbl).replace("[A]", "").strip()
    m = re.match(r"^(\d)Q(\d\d)$", t)
    if m: return "%sQ%02d" % (m.group(1), int(m.group(2)) - 1)
    m = re.match(r"^(\d)H(\d\d)$", t, re.I)
    if m: return "%sH%02d" % (m.group(1), int(m.group(2)) - 1)
    m = re.match(r"^(\d{4})$", t)
    if m: return "%d" % (int(m.group(1)) - 1)
    return None

def _dl_year_ago_col(heads, periods):
    if not periods: return None
    want = _dl_year_ago_label(heads[periods[-1]])
    if want is None: return None
    for i in periods:
        if str(heads[i]).replace("[A]", "").strip().upper() == want.upper():
            return i
    return None

def derived_lint(html):
    issues = []
    for heads, body in _dl_rows(html):
        ai = _dl_col(heads, r"^actual")
        ci = _dl_col(heads, r"consensus")
        di = _dl_col(heads, r"vs\s*cons|Δ")
        yi = _dl_col(heads, r"^y/y$")
        if ai is not None and ci is not None and di is not None:
            for tds in body:
                name = re.sub(r"<[^>]+>", "", tds[0]).strip()
                a, c, d = _dl_val(tds[ai]), _dl_val(tds[ci]), _dl_val(tds[di])
                if not (a and c and d): continue
                if a[1] != c[1]:
                    issues.append("MAGNITUDE: row %r prints Actual %s against Consensus %s - "
                                  "two magnitudes in one comparison."
                                  % (name, _dl_txt(tds[ai]), _dl_txt(tds[ci])))
                    continue
                if a[0] * c[0] < 0 or c[0] == 0:
                    if d[1] in ("%", "pp", "bps"):
                        issues.append("SIGN BOUNDARY: row %r prints Actual %s against Consensus "
                                      "%s and a percent surprise of %s. A ratio across zero "
                                      "inverts the verdict; print the absolute swing."
                                      % (name, _dl_txt(tds[ai]), _dl_txt(tds[ci]), _dl_txt(tds[di])))
                    continue
                if _dl_pct_unit(a[1]):
                    exp, tol = a[0] - c[0], a[2] + c[2]
                else:
                    exp = 100.0 * (a[0] - c[0]) / abs(c[0])
                    tol = 100.0 * (a[2] + abs(a[0] / c[0]) * c[2]) / abs(c[0])
                tol += d[2]
                if _dl_txt(tds[ai]) == _dl_txt(tds[ci]) and abs(d[0]) > d[2]:
                    issues.append("UNVERIFIABLE PRECISION: row %r prints Actual and Consensus "
                                  "both as %s while the surprise column prints %s. The figures "
                                  "differ, but not at the precision the row is printed to, so a "
                                  "reader cannot check the line. Print the two cells finer - %s - "
                                  "or drop the surprise."
                                  % (name, _dl_txt(tds[ai]), _dl_txt(tds[di]),
                                     _dl_finer(a[1])))
                elif tol > max(abs(d[0]), 10 ** -d[2] if d[2] else 1.0):
                    issues.append("UNVERIFIABLE PRECISION: row %r prints a surprise of %s that its "
                                  "own cells (%s / %s) support only to +-%.2f. A reader cannot "
                                  "check the row; print the figures to the precision the claim needs."
                                  % (name, _dl_txt(tds[di]), _dl_txt(tds[ai]), _dl_txt(tds[ci]), tol))
                elif abs(exp - d[0]) > tol:
                    issues.append("DELTA DOES NOT RECONCILE: row %r prints a surprise of %s, but "
                                  "%s against %s re-derives %+.2f (tolerance +-%.2f from the "
                                  "printed rounding). The table contradicts itself."
                                  % (name, _dl_txt(tds[di]), _dl_txt(tds[ai]),
                                     _dl_txt(tds[ci]), exp, tol))
        elif yi is not None and yi == len(heads) - 1 and _dl_col(heads, r"\[A\]") is not None:
            per = [i for i, h in enumerate(heads) if re.search(r"\[A\]", h)]
            if len(per) < 2: continue
            ya = _dl_year_ago_col(heads, per)
            if ya is None or ya == per[-1]: continue
            for tds in body:
                name = re.sub(r"<[^>]+>", "", tds[0]).strip()
                f, l, y = _dl_val(tds[ya]), _dl_val(tds[per[-1]]), _dl_val(tds[yi])
                if not (f and l and y): continue
                us = {v[1] for v in (_dl_val(tds[i]) for i in per) if v}
                if len(us) > 1:
                    issues.append("MAGNITUDE: row %r mixes %s across its own period series."
                                  % (name, sorted(us)))
                    continue
                if f[0] * l[0] < 0 or f[0] == 0:
                    if y[1] in ("%",):
                        issues.append("SIGN BOUNDARY: row %r prints Y/Y %s across a series that "
                                      "crosses zero (%s -> %s). Print the absolute swing."
                                      % (name, _dl_txt(tds[yi]), _dl_txt(tds[ya]),
                                         _dl_txt(tds[per[-1]])))
                    continue
                if _dl_pct_unit(f[1]):
                    exp, tol = l[0] - f[0], f[2] + l[2]
                else:
                    exp = 100.0 * (l[0] - f[0]) / abs(f[0])
                    tol = 100.0 * (l[2] + abs(l[0] / f[0]) * f[2]) / abs(f[0])
                tol += y[2]
                if abs(exp - y[0]) > tol:
                    issues.append("HISTORY Y/Y DOES NOT RECONCILE: row %r prints %s, but its own "
                                  "series %s -> %s re-derives %+.2f (tolerance +-%.2f). The two "
                                  "cells the reader can see refute the third."
                                  % (name, _dl_txt(tds[yi]), _dl_txt(tds[ya]),
                                     _dl_txt(tds[per[-1]]), exp, tol))
    return issues

def _dl_txt(cell):
    t = re.sub(r"<[^>]+>", "", str(cell))
    return t.replace("&#8593;", "").replace("&#8595;", "").replace("&nbsp;", " ").strip()

def _dl_finer(unit):
    return {"%": "a finer figure in the source", "": "`2d`, or a magnitude-carrying count unit "
            "(`#K`/`#M`/`#B`)"}.get(unit, "a finer unit token from the vocabulary in SKILL.md")

def _dl_pct_unit(u):
    return u in ("%", "pp", "bps")

try:
    from render_wrap import LOWER_IS_BETTER_CUES as _DL_LOWER_BETTER, \
                            HIGHER_IS_BETTER_EXEMPT as _DL_HIGHER_BETTER_EXEMPT
except ImportError:
    import importlib.util as _ilu
    _spec = _ilu.spec_from_file_location(
        "_rw_cues", os.path.join(os.path.dirname(os.path.abspath(__file__)), "render_wrap.py"))
    _rw = _ilu.module_from_spec(_spec); _spec.loader.exec_module(_rw)
    _DL_LOWER_BETTER = _rw.LOWER_IS_BETTER_CUES
    _DL_HIGHER_BETTER_EXEMPT = _rw.HIGHER_IS_BETTER_EXEMPT

def _dl_lower_better(label):
    t = re.sub(r"\([^()]*\)", " ", str(label))
    t = re.sub(r"[^a-z0-9 ]", " ", t.lower())
    t = re.sub(r"\s+", " ", t)
    if any(k in t for k in _DL_HIGHER_BETTER_EXEMPT):
        return False
    return any(k in t for k in _DL_LOWER_BETTER)

_PC_FAVOURABLE = re.compile(r'class="[^"]*\bup\b[^"]*"')
_PC_DATA_LT = re.compile(r'<tr[^>]*\bdata-lt="([^"]+)"')

_PC_REG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "polarity_registry.json")
_PC_REG = None

def _pc_registry():
    global _PC_REG
    if _PC_REG is None:
        try:
            with open(_PC_REG_PATH, encoding="utf-8") as fh:
                _PC_REG = (json.load(fh) or {}).get("codes") or {}
        except (OSError, ValueError):
            _PC_REG = {}
    return _PC_REG

def _pc_lower_better(label, ltcode=None):
    e = _pc_registry().get(str(ltcode or "").strip())
    if e is not None:
        return bool(e.get("lower_is_better"))
    return _dl_lower_better(label) or None

def polarity_colour_lint(html):
    issues = []
    for tb in re.findall(r"<table[^>]*>(.*?)</table>", html, re.S):
        for tr in re.findall(r"<tr[^>]*>.*?</tr>", tb, re.S):
            if re.match(r'<tr[^>]*class="[^"]*\bnoinv\b', tr): continue
            tds = re.findall(r"(<td[^>]*>.*?</td>)", tr, re.S)
            if len(tds) < 2: continue
            label = re.sub(r"<[^>]+>", "", tds[0]).strip()
            if not label: continue
            _m = _PC_DATA_LT.match(tr)
            if not _pc_lower_better(label, _m.group(1) if _m else None): continue
            for cell in tds[1:]:
                txt = re.sub(r"<[^>]+>", "", cell)
                txt = txt.replace("&#8593;", "").replace("&#8595;", "").strip()
                if not re.match(r"^\+\s*[\d.]", txt): continue
                if _PC_FAVOURABLE.search(cell):
                    issues.append(
                        "POLARITY COLOUR: row %r prints a rise of %s in the favourable colour. "
                        "Lower is better on this metric, so a rise is bad news and must read "
                        "red. Declare the row in `snapshot.inv` or as `inv: true` on the "
                        "`results` row - one declaration covers every table."
                        % (label, txt))
    return issues

_DL_PROSE_NUM = re.compile(r"(?<![\d.])(\d+\.\d+)\s*(%|pp|bps|x)")
_DL_DELTA_HEAD = re.compile(r"(?i)^(y/y|q/q|h/h|prior fy|prior period|change|"
                            r"&Delta;\s*vs\s*cons|\u0394\s*vs\s*cons|change \(y/y\))$")

def _dl_decimals(t):
    return len(t.split(".", 1)[1]) if "." in t else 0

def _dl_printed_deltas(html):
    out = {}
    for heads, body in _dl_rows(html):
        cols = [i for i, h in enumerate(heads) if _DL_DELTA_HEAD.match(h.strip())]
        if not cols:
            continue
        for tds in body:
            for i in cols:
                if i >= len(tds):
                    continue
                txt = _dl_txt(tds[i])
                m = re.match(r"^[-+]?\s*(\d+(?:\.\d+)?)\s*(%|pp|bps|x)$", txt)
                if m:
                    out.setdefault(m.group(2), set()).add(m.group(1))
    return out

def _dl_prose(html):
    x = re.sub(r"<style[^>]*>.*?</style>", " ", html, flags=re.S | re.I)
    x = re.sub(r"<script[^>]*>.*?</script>", " ", x, flags=re.S | re.I)
    x = re.sub(r"<table[^>]*>.*?</table>", " ", x, flags=re.S)
    return re.sub(r"<[^>]+>", " ", x)

def _dl_printed_actuals(html):
    out = {}
    for heads, body in _dl_rows(html):
        cols = [i for i, h in enumerate(heads) if re.match(r"(?i)^actual", h.strip())]
        for tds in body:
            for i in cols:
                if i >= len(tds): continue
                m = re.match(r"^[-+]?\s*[\u00a3\u20ac\u00a5$]?\s*([\d,]+(?:\.\d+)?)\s*"
                             r"(%|pp|bps|x|B|M|T|K)?$", _dl_txt(tds[i]))
                if m:
                    out.setdefault(m.group(2) or "", set()).add(m.group(1))
    return out

def prose_precision_lint(html):
    printed = _dl_printed_deltas(html)
    for u, vals in _dl_printed_actuals(html).items():
        printed.setdefault(u, set()).update(vals)
    if not printed:
        return []
    prose = _dl_prose(html)
    issues = []
    for m in _DL_PROSE_NUM.finditer(prose):
        raw, unit = m.group(1), m.group(2)
        cells = printed.get(unit) or set()
        if raw in cells:
            continue
        _sig = raw.rstrip("0").rstrip(".") if "." in raw else raw
        if any((c.rstrip("0").rstrip(".") if "." in c else c) == _sig for c in cells):
            continue
        pd = _dl_decimals(raw)
        for cell in sorted(cells):
            cd = _dl_decimals(cell)
            if cd >= pd:
                continue
            try:
                if abs(float(raw) - float(cell)) <= 0.5 * 10 ** (-cd) + 1e-9:
                    issues.append(
                        "PROSE PRECISION: the note writes %s%s in prose while the table prints "
                        "%s%s for the same figure. The renderer owns every table cell's format, so "
                        "quote it as printed - prose may round a printed figure coarser, never "
                        "finer." % (raw, unit, cell, unit))
                    break
            except ValueError:
                pass
    return sorted(set(issues))

LAYERS = [("house_asserts", house_asserts), ("format_lint", format_lint),
          ("wrap_eval", wrap_eval), ("numbers_lint", numbers_lint),
          ("dedup_lint", dedup_lint), ("content_lint", content_lint),
          ("palette_lint", palette_lint),
    ("crosstable_lint", crosstable_lint),
    ("derived_lint", derived_lint),
    ("polarity_colour_lint", polarity_colour_lint),
    ("prose_precision_lint", prose_precision_lint),
]

def validate(html, css_src=None):
    out = {}
    for name, fn in LAYERS:
        out[name] = fn(html, css_src) if name == "palette_lint" else fn(html)
    return out

if __name__ == "__main__":
    import sys
    html = open(sys.argv[1], encoding="utf-8").read()
    res = validate(html)
    failed = False
    for name, issues in res.items():
        if issues:
            failed = True
            print(f"[FAIL] {name} ({len(issues)}):")
            for m in issues:
                print("   - " + str(m))
        else:
            print(f"[PASS] {name}")
    print("\nRESULT:", "FAIL -- fix the note, not the eval" if failed else "ALL LAYERS PASS")
    sys.exit(1 if failed else 0)
