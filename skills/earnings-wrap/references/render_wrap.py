#!/usr/bin/env python3
"""
render_wrap.py — deterministic Earnings Wrap renderer (InSync house format).

Python/weasyprint port of the reference build_new_wraps.js. Layout lives in THIS
code; the model only supplies a config of prose + InSync numbers. That makes every
wrap look identical across tickers, chats and machines (the JPM_wrap reference look):
light bordered masthead, Executive Summary box, then the fixed 10 sections.

Usage:  python references/render_wrap.py <config.json> <out.pdf>
        (runs kpi_gate on the Primary KPI first; refuses to render on FAIL)

Pure Python: weasyprint only (no Node, no LibreOffice). print-color-adjust:exact is
set so the fills survive even a browser Print-to-PDF fallback.
"""
import sys, os, json, base64, subprocess, re

HERE = os.path.dirname(os.path.abspath(__file__))

VERSION = "7.13.1"

BROKER_FLOOR = 1

FIRM_NAME        = "InSync Analytics"
BRAND_ORANGE_WORD = "InSync"
BRAND_INK_WORD    = " Analytics"
SUFFIX_TEMPLATE   = " ({TICKER}) - {PERIOD}"
PERIOD_FORMAT     = "short"
LOGO_FILE         = "insync-logo-mark.png"
MARK_PX_W, MARK_PX_H = 59, 46
DARK="#1A1A1A"; ORANGE="#E8850C"; GREEN="#1E7A34"; RED="#B02A1F"; GREY="#6E6E6E"
INK="#222222"; BOX="#C9C9C9"; GRY="#555555"; BAND="#F0E9D8"; HEADBG="#EDEDED"
EXBG="#F6F3EE"; TAKE="#F6F3EE"; LABEL="#B25E08"; FOOT="#6E6E6E"; GRID="#DDDDDD"
ZEBRA="#F5F0E8"; HRULE="#BBBBBB"; RRULE="#EEEEEE"
PALETTE={"DARK":DARK,"ORANGE":ORANGE,"GREEN":GREEN,"RED":RED,"GREY":GREY,"INK":INK,
         "BOX":BOX,"GRY":GRY,"BAND":BAND,"HEADBG":HEADBG,"EXBG":EXBG,"TAKE":TAKE,
         "LABEL":LABEL,"FOOTC":FOOT,"GRID":GRID,"ZEBRA":ZEBRA,"HRULE":HRULE,"RRULE":RRULE}

def esc(s):
    return (str(s).replace("&","&amp;").replace("<","&lt;").replace(">","&gt;"))

def md(s):
    out=[]; parts=str(s).split("**")
    for i,p in enumerate(parts):
        out.append(("<b>"+esc(p)+"</b>") if i%2 else esc(p))
    return "".join(out)

def pct(a,b): return (a/b-1)*100.0

_SPCT_ZERO = re.compile(r"^[-+]?[^0-9]*0(?:\.0+)?\s*(?:%|pp|bps|x)?$")

from decimal import Decimal, ROUND_HALF_UP

def house_round(v, dp):
    if v is None:
        return None
    q = Decimal(1).scaleb(-int(dp))
    return float(Decimal(repr(float(v))).quantize(q, rounding=ROUND_HALF_UP))

def house_fmt(v, dp, sep=False):
    q = Decimal(1).scaleb(-int(dp))
    d = Decimal(repr(float(v))).quantize(q, rounding=ROUND_HALF_UP)
    if d == 0:
        d = abs(d)
    return ("{:,.{p}f}" if sep else "{:.{p}f}").format(d, p=int(dp))

_MAX_DEC, _MAX_PCT_DEC = 4, 2

def _unit_dp(u):
    if str(u).startswith("%"): return PCT_DP
    if u in ("x", "2d", "bn"): return 2
    if u in _COUNT_MAG:        return COUNT_DP
    if u == "#":               return 0
    m = _UNIT_RE.match(u or "")
    return MONEY_DP if (m and (m.group(1) or m.group(2))) else None

def _flash_verdict(a, c, u):
    if a is None or c is None or not c: return None
    if str(u).startswith("%"):
        d = a - c
        return 1 if d >= 0.25 else (-1 if d <= -0.25 else 0)
    d = (a - c) / abs(c) * 100.0
    return 1 if d > 1.0 else (-1 if d < -1.0 else 0)

def row_dp(a, c, py, u):
    if u == "#": return 0
    dp = _unit_dp(u)
    if dp is None: return None
    cap = _MAX_PCT_DEC if str(u).startswith("%") else _MAX_DEC
    gaps = [(x, y) for x, y in ((a, c), (a, py))
            if x is not None and y is not None and x != y]
    while dp < cap and any(house_round(x, dp) == house_round(y, dp) for x, y in gaps):
        dp += 1
    want = _flash_verdict(a, c, u)
    if want is not None:
        while dp < cap and _flash_verdict(house_round(a, dp),
                                          house_round(c, dp), u) != want:
            dp += 1
    if (a is not None and c is not None and c and a * c > 0
            and not str(u).startswith("%")):
        def _tol(d):
            h = 0.5 * 10 ** (-d)
            qa, qc = house_round(a, d), house_round(c, d)
            if not qc: return float("inf"), 0.0
            return (100.0 * (h + abs(qa / qc) * h) / abs(qc) + 0.05,
                    abs(100.0 * (qa - qc) / abs(qc)))
        while dp < cap:
            t, sv = _tol(dp)
            if t <= max(sv, 10 ** -0.05):
                break
            dp += 1
    return dp

def results_dp_map(cfg):
    if not isinstance(cfg, dict):
        return {}
    return {r.get("name"): row_dp(r.get("a"), r.get("c"), r.get("py"), r.get("u"))
            for sec in (cfg.get("results") or ())
            for r in (sec.get("rows") or ())}

_CTK_STRIP  = re.compile(r"\b(total|adj|adjusted|consolidated|reported)\b")
_CTK_PLURAL = re.compile(r"(?<=[a-z])s\b")

def _ct_key(label):
    s = re.sub(r"<[^>]+>", " ", label or "")
    s = re.sub(r"&[a-z]+;", " ", s)
    s = re.sub(r"\([^)]*\)", " ", s)
    s = _CTK_STRIP.sub(" ", s.lower())
    s = re.sub(r"[^a-z0-9\- ]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return _CTK_PLURAL.sub("", s)

def shared_results_dp(cfg):
    if not isinstance(cfg, dict):
        return {}
    dpm = results_dp_map(cfg); out = {}; dup = set()
    for sec in (cfg.get("results") or ()):
        for r in (sec.get("rows") or ()):
            k = _ct_key(r.get("name"))
            if not k: continue
            if k in out: dup.add(k); continue
            out[k] = (r.get("u"), dpm.get(r.get("name")))
    for k in dup: out.pop(k, None)
    return out

def dp_quantizer(dp):
    if dp is None:
        return lambda v: v
    return lambda v: house_round(v, dp)

def widened_rows(cfg):
    out = []
    for sec in ((cfg or {}).get("results") or ()):
        for r in (sec.get("rows") or ()):
            u = r.get("u")
            base, dp = _unit_dp(u), row_dp(r.get("a"), r.get("c"), r.get("py"), u)
            if base is not None and dp is not None and dp > base:
                out.append((str(r.get("name")), base, dp))
    return out

def widened_disclosure(rows):
    if not rows:
        return ""
    return ("Precision note: %s print more decimals than the house format - at the house "
            "precision the surprise on those rows would have rounded away or changed "
            "direction." % ", ".join(n for n, _b, _d in rows))

MONEY_DP = 2
PCT_DP   = 1
COUNT_DP = 2

RETIRED_UNITS = {
    "%2": ("%",  "percentages print ONE decimal under the house policy (74.9%, not 74.85%); "
                 "a Results row that NEEDS more gets it from row_dp, not from the config"),
    "1d": ("2d", "bare numbers print TWO decimals under the house policy (64.44, not 64.4)"),
}

def _crosses_zero(a, b):
    if a is None or b is None: return False
    return (a * b < 0) or b == 0

def change(a, b, u, absolute_unit=None, dp=None):
    if a is None or b is None: return ("&mdash;", 0.0, False)
    if str(u).startswith("%"):
        d = _xdiff(a, b)
        return (_spp(d), d, False)
    if _crosses_zero(a, b):
        d = a - b
        body = fmtVal(abs(d), absolute_unit or u, dp)
        return (("+" + body) if d > 0 else ("-" + body if d < 0 else body), d, False)
    s = (a - b) / abs(b) * 100.0
    return (_spct(s), s, True)

def _xdiff(a, b):
    return float(Decimal(repr(a)) - Decimal(repr(b)))

def _spp(d):
    d=house_round(d,PCT_DP)
    return "0.0pp" if d==0 else (("+" if d>0 else "")+house_fmt(d,PCT_DP)+"pp")
def _spct(s):
    s=house_round(s,PCT_DP)
    return "0.0%" if s==0 else (("+" if s>0 else "")+house_fmt(s,PCT_DP)+"%")
_UNIT_RE=re.compile(r'^((?-i:GBPence|GBp|ZARc|USDc|EURc)|'
                    r'R\$|A\$|C\$|HK\$|NT\$|[$\u20ac\u00a3\u00a5\u20b9\u20a9\u20bd\u20ba]|'
                    r'EUR|GBP|CHF|DKK|SEK|NOK|CAD|JPY|BRL|MXN|ZAR|INR|KRW|TWD|HKD|CNY|'
                    r'SGD|AUD|NZD|PLN|TRY|ILS|AED|SAR|THB|PHP|MYR)?(B|T|M)?$')
_SUBUNITS=("GBPence","GBp","ZARc","USDc","EURc")
_MONEY_MAG_DP={"B":MONEY_DP,"T":MONEY_DP,"M":MONEY_DP,"":MONEY_DP}
def _money(v,sym,mag,dp=None):
    sym=sym or "$"
    if sym in _SUBUNITS: sym=sym+"\u2009"
    elif len(sym)==3 and sym.isalpha(): sym=sym.upper()+"\u2009"
    mag=(mag or "").upper()
    _house=_MONEY_MAG_DP.get(mag)
    if _house is None:
        raise SystemExit(
            "UNIT GATE FAIL: magnitude %r is not a house magnitude. Legal magnitudes are "
            "B, T and M (upper case), or none at all for a per-share or unit figure." % mag)
    dp=_house if dp is None else dp
    body=house_fmt(abs(v),dp)+mag
    return ("-"+sym+body) if v<0 else (sym+body)
UNIT_VOCABULARY = {
    "%":   "rate, ONE decimal (45.1%). Every percentage on every note. Differenced in pp, "
           "never ratioed.",
    "x":   "multiple, two decimals (24.80x).",
    "2d":  "bare number, two decimals (16.72). Use when no unit fits.",
    "bn":  "unitless billions (2.00bn). Prefer a currency token.",
    "#":   "count in whole units (1,482). Refuses a fractional value.",
    "#K":  "count in thousands, two decimals (149.25K).",
    "#M":  "count in millions, two decimals (16.72M).",
    "#B":  "count in billions, two decimals (3.72B).",
    "<cur>[B|T|M]": "money: a currency token, optionally with a magnitude - $B, $, EURB, "
                    "GBpM, CHF, \u00a5T. TWO decimals at every magnitude, per-share "
                    "included ($39.31B, $617.10M, $1.87).",
}
_COUNT_MAG = {"#K": ("K", COUNT_DP), "#M": ("M", COUNT_DP), "#B": ("B", COUNT_DP)}

def _count(v, mag, dp):
    return house_fmt(v, dp) + mag

def _retired_unit(u):
    repl, why = RETIRED_UNITS[u]
    return ("UNIT GATE FAIL: unit token %r was retired in v7.7.0. Use %r instead: %s. "
            "One precision per kind of figure is the whole point - a note that prints "
            "74.85%% on one margin row and 65.9%% on the next is a note whose rounding the "
            "reader has to reverse-engineer." % (u, repl, why))

def fmtVal(v,u,dp=None):
    if v is None: return "&mdash;"
    if u in RETIRED_UNITS: raise SystemExit(_retired_unit(u))
    if u=="%":  return house_fmt(v,PCT_DP if dp is None else dp)+"%"
    if u=="x":  return house_fmt(v,2 if dp is None else dp)+"x"
    if u=="2d": return house_fmt(v,2 if dp is None else dp)
    if u=="bn": return house_fmt(v,2 if dp is None else dp)+"bn"
    if u in _COUNT_MAG:
        mag,_d=_COUNT_MAG[u]; return _count(v,mag,_d if dp is None else dp)
    if u=="#":
        if abs(v-house_round(v,0))>1e-9:
            raise SystemExit(
                "UNIT GATE FAIL: u='#' means a count in WHOLE units and the value is %r. "
                "A count expressed in millions or thousands must say so - use '#M' or '#K' "
                "- or the figure prints rounded to whole units with its magnitude lost "
                "(16.721 became '17'). See the unit vocabulary in SKILL.md." % v)
        return house_fmt(v, 0, sep=True)
    m=_UNIT_RE.match(u or "")
    if m and (m.group(1) or m.group(2)): return _money(v,m.group(1),m.group(2),dp)
    raise SystemExit(
        "UNIT GATE FAIL: unit token %r is not in the vocabulary, so the figure would print "
        "as a bare float with no currency and no magnitude (39.306 instead of $39.31B). "
        "Legal tokens: %s. See the unit vocabulary in SKILL.md."
        % (u, ", ".join(sorted(UNIT_VOCABULARY))))
def yoy(a,b,u):
    return change(a,b,u)[0]
def deltaCons(a,c,u,inv=False,dp=None):
    if a is None or c is None: return ("&mdash;",0)
    dtxt,raw,is_ratio=change(a,c,u,None,dp)
    if not is_ratio and not str(u).startswith("%"):
        sign=0 if _SPCT_ZERO.match(dtxt) else (1 if raw>0 else (-1 if raw<0 else 0))
    else:
        thr=0.15 if str(u).startswith("%") else 0.5
        sign=1 if raw>thr else (-1 if raw<-thr else 0)
    if inv: sign=-sign
    return (dtxt,sign)

def pctcols(w):
    tot=float(sum(w)); return [100.0*x/tot for x in w]
def colgroup(w):
    return "<colgroup>"+"".join('<col style="width:%.3f%%">'%p for p in pctcols(w))+"</colgroup>"

def secH(n,title):
    lead=(f'<span class="sn">{esc(n)}</span> ' if n else "")
    return f'<h2 class="sec">{lead}{esc(title)}</h2>'
def sub(t): return f'<div class="sub">{esc(str(t).upper())}</div>'
def cap(t): return f'<div class="cap">{esc(t)}</div>' if t else ""
def bullets(items, field=None):
    if isinstance(items, str):
        raise SystemExit("CONFIG SHAPE FAIL%s: a label/bullets pair carries a bare string where "
                         "a LIST of bullets is required. Wrap it: [\"label\", [\"bullet\"]]. "
                         "Left as a string it renders one bullet per character."
                         % (" in " + field if field else ""))
    return '<ul class="r">'+"".join(f"<li>{md(b)}</li>" for b in items)+"</ul>"
def monitorBullets(items):
    lis=[]
    for it in items[:5]:
        if isinstance(it,dict):
            m=md("**"+str(it["metric"])+"**")
            f=esc(it["fact"]); mo=esc(it["monitor"])
            lis.append('<li>'+m+' <span class="mflab">Fact:</span> '+f+
                       ' <span class="mflab">Monitor:</span> '+mo+'</li>')
        else:
            lis.append("<li>"+md(it)+"</li>")
    return '<ul class="r">'+"".join(lis)+"</ul>"

class _Zebra:
    __slots__=("i",)
    def __init__(self): self.i=-1
    def tr(self):
        self.i+=1
        return '<tr class="z">' if self.i%2 else "<tr>"

_FIG_CUR = r"(?:R\$|A\$|C\$|HK\$|NT\$|[$\u20ac\u00a3\u00a5\u20b9\u20a9\u20bd\u20ba]|" \
           r"GBPence|GBp|ZARc|USDc|EURc|EUR|GBP|CHF|DKK|SEK|NOK|CAD|JPY|BRL|MXN|ZAR|INR|" \
           r"KRW|TWD|HKD|CNY|SGD|AUD|NZD|PLN|TRY|ILS|AED|SAR|THB|PHP|MYR)"
_FIGURE_RE = re.compile(
    r"(?:" + _FIG_CUR + r"\s?-?\d[\d,]*(?:\.\d+)?" r")"
    r"|(?:-?\d[\d,]*(?:\.\d+)?\s?(?:%|x\b|pp\b|bps\b|[BTMK]\b))")

SUPPRESSED_CELL = "—"


PEER_MIN = 1

def _peer_cell(c):
    if not (isinstance(c, dict) and "peers" in c):
        return c
    p = c.get("peers")
    vals = [float(v) for v in (p.values() if isinstance(p, dict) else (p or []))
            if isinstance(v, (int, float)) and not isinstance(v, bool)]
    out = {k: v for k, v in c.items() if k != "peers"}
    if len(vals) < PEER_MIN:
        return "n/a"
    vals.sort()
    k = len(vals) // 2
    out["v"] = vals[k] if len(vals) % 2 else (vals[k - 1] + vals[k]) / 2.0
    return out

def _cell_text(c):
    c = _peer_cell(c)
    if c is None:
        return SUPPRESSED_CELL
    if isinstance(c, str) and not c.strip():
        return SUPPRESSED_CELL
    if isinstance(c, dict):
        u = c.get("u")
        if c.get("v") is None and c.get("lo") is None and c.get("hi") is None:
            return str(c.get("note") or "").strip() or SUPPRESSED_CELL
        if "lo" in c or "hi" in c:
            body = fmtVal(c.get("lo"), u) + " to " + fmtVal(c.get("hi"), u)
        else:
            body = fmtVal(c.get("v"), u)
        n = str(c.get("note") or "").strip()
        return (body + " " + n) if n else body
    return str(c)

def _cell_figures(c):
    return _FIGURE_RE.findall(_cell_text(c))

def tbl(head,rows,w,delta_cols=None,inv_rows=(),noinv_rows=()):
    if (not w) or len(w)!=len(head): w=[1]*len(head)
    h="".join(f'<th class="{ "l" if i==0 else "r" }">{esc(c)}</th>' for i,c in enumerate(head))
    body=""; z=_Zebra()
    for i,r in enumerate(rows):
        tds=""; inv = i in inv_rows
        dc = delta_cols[i] if isinstance(delta_cols, list) else delta_cols
        for j,c in enumerate(r):
            c = _cell_text(c)
            cell=esc(c); cls="l" if j==0 else "r"
            s=str(c)
            decorate = (j>0) if dc is None else (j in dc)
            if decorate and s.startswith("+"):
                cell='<span class="%s">&#8593; %s</span>'%("dn" if inv else "up",esc(c))
            elif decorate and len(s)>1 and s[0]=="-" and s[1] in "0123456789$.":
                cell='<span class="%s">&#8595; %s</span>'%("up" if inv else "dn",esc(c))
            tds+=f'<td class="{cls}">{cell}</td>'
        _tr = z.tr()
        if i in noinv_rows:
            _tr = (_tr.replace('<tr class="', '<tr class="noinv ')
                   if 'class="' in _tr else '<tr class="noinv">')
        body+=_tr+tds+"</tr>"
    return f'<table class="data">{colgroup(w)}<tr>{h}</tr>{body}</table>'

_RESTATE_PREFIX = re.compile(r"^R[0-9]*-")

def _norm_period(q):
    s = str(q or "").strip()
    s = _RESTATE_PREFIX.sub("", s)
    return re.sub(r"(?i)^(\d)([QH])(\d\d)$", lambda m: m.group(1)+m.group(2).upper()+m.group(3), s)

def _year_ago(q):
    s=_norm_period(q)
    m=re.match(r"(\d)Q(\d\d)$", s)
    if m: return "%sQ%02d" % (m.group(1), int(m.group(2))-1)
    m=re.match(r"(\d)H(\d\d)$", s, re.I)
    if m: return "%sH%02d" % (m.group(1), int(m.group(2))-1)
    m=re.match(r"(\d{4})$", s)
    if m: return "%d" % (int(m.group(1))-1)
    return None

_SEQ_HEADS = {"quarterly": "Q/Q", "half_yearly": "H/H", "annual": "Prior FY"}

def _cadence(q):
    s = _norm_period(q)
    if re.match(r"(\d)Q(\d\d)$", s): return "quarterly"
    if re.match(r"(\d)H(\d\d)$", s, re.I): return "half_yearly"
    if re.match(r"(\d{4})$", s): return "annual"
    return None

def _seq_head(q):
    return _SEQ_HEADS.get(_cadence(q), "Prior Period")

def _fmt_pct(d):  return _spct(d)
def _fmt_pp(d):   return _spp(d)

def kpiHistoryTable(kt, inv=(), noinv=(), shared_dp=None):
    qs=kt["quarters"]; rows=kt["rows"]
    ya=_year_ago(qs[-1]); ya_idx=qs.index(ya) if ya in qs else None
    if ya_idx is None and not all(r.get("yoy") for r in rows):
        sys.stderr.write(
            "WARN kpiHistoryTable: no year-ago period for %r inside the window %r, so Y/Y "
            "falls back to each row's explicit 'yoy' and prints n/a where none is supplied. "
            "Expected the same period one year back (2Q26->2Q25, 1H26->1H25, 2026->2025); "
            "widen the window or supply 'yoy' per row.\n" % (qs[-1], list(qs)))
    head=["KPI"]+[str(q)+" [A]" for q in qs]+["Y/Y"]
    orows=[]
    for r in rows:
        u=r.get("u","2d"); kind=r.get("kind","level"); vals=r["vals"]
        cells=[r["name"]]
        for v in vals:
            if v is None: cells.append("n/a"); continue
            if kind=="growth":
                cells.append((("+" if v>=0 else "")+house_fmt(v,PCT_DP)+"%"))
            else:
                cells.append(fmtVal(v,u))
        yy=r.get("yoy")
        if yy is None and ya_idx is not None:
            f=vals[ya_idx]; l=vals[-1]
            _sh=(shared_dp or {}).get(_ct_key(r["name"]))
            if _sh is not None and _sh[0]==u and _sh[1] is not None and kind!="growth":
                f=f if f is None else house_round(f,_sh[1])
                l=l if l is None else house_round(l,_sh[1])
            if f is not None and l is not None:
                if kind in ("rate","growth") or str(u).startswith("%"):
                    yy=_fmt_pp(_xdiff(l,f))
                else:
                    yy=change(l,f,u)[0]
        cells.append(yy if yy else "n/a")
        orows.append(cells)
    n=len(qs); w=kt.get("w")
    if (not w) or len(w)!=(n+2): w=[3000]+[1150]*n+[1300]
    yy_col = len(head) - 1
    canon = {str(x).strip().lower() for x in (inv or ())}
    ncanon = {str(x).strip().lower() for x in (noinv or ())}
    inv_rows = {i for i, r in enumerate(rows) if _canon_metric(r["name"]) in canon}
    noinv_rows = {i for i, r in enumerate(rows) if _canon_metric(r["name"]) in ncanon}
    per_row = [set(range(1, yy_col + 1)) if r.get("kind") == "growth" else {yy_col}
               for r in rows]
    return tbl(head, orows, w, delta_cols=per_row, inv_rows=inv_rows,
               noinv_rows=noinv_rows)


def _canon_metric(name):
    t = str(name)
    while True:
        t2 = re.sub(r"[\s*\u2020\u2021#!~^]+$", "", t)
        t2 = re.sub(r"\s*\([^()]*\)\s*$", "", t2)
        if t2 == t: break
        t = t2
    t = re.sub(r"[^a-z0-9 ]", " ", t.lower())
    return re.sub(r"\s+", " ", t).strip()

def _polarity_set(cfg):
    out = set()
    for x in ((cfg.get("snapshot") or {}).get("inv") or ()):
        out.add(_canon_metric(x))
    for sec in (cfg.get("results") or ()):
        for r in (sec.get("rows") or ()):
            if r.get("inv"): out.add(_canon_metric(r.get("name")))
    return out

def _polarity_noinv(cfg):
    return {_canon_metric(x) for x in ((cfg.get("snapshot") or {}).get("noinv") or ())}

LOWER_IS_BETTER_CUES = (
    "combined ratio", "loss ratio", "expense ratio", "efficiency ratio",
    "cost income ratio", "cost to income", "cost of risk", "operating ratio",
    "credit loss", "provision for", "provisions for", "loan loss provision",
    "delinquenc", "charge off", "chargeoff", "attrition", "churn", "vacancy",
    "net debt", "net leverage", "debt to ebitda", "days sales outstanding",
    "heat rate", "cost per",
    "interest expense", "capex", "capital expenditure",
    "operation and maintenance", "operations and maintenance", "o and m",
    "fuel and purchased", "fuel expense", "cost of goods", "cost of sales",
    "cost of revenue", "operating expense", "operating expenses", "opex",
    "selling general", "impairment", "catastrophe", "accident year", "ex cat",
    "days inventory", "days payable", "restructuring",
)

HIGHER_IS_BETTER_LABEL = (
    "revenue", "sales", "bookings", "backlog", "volume", "shipment", "delivery", "deliveries",
    "subscriber", "subscription", "member", "customer", "account", "user", "arr", "acv", "gmv",
    "aum", "premium", "deposit", "loan", "fcf", "free cash flow", "operating cash",
    "eps", "earnings per share", "net income", "profit", "pbt", "ebit", "ebitda", "gross profit",
    "nim", "net interest income", "fee income", "dividend per share", "dps", "starts",
    "occupancy", "same store noi", "production", "capacity", "throughput", "units",
)

HIGHER_IS_BETTER_EXEMPT = (
    "coverage", "tier 1", "tier1", "cet1", "regulatory capital", "capital ratio",
    "solvency", "liquidity", "reserve ratio", "funding ratio",
)

def _reads_lower_is_better(label, canon):
    c = canon(label)
    if any(k in c for k in HIGHER_IS_BETTER_EXEMPT):
        return False
    return any(k in c for k in LOWER_IS_BETTER_CUES)

def polarity_gate(cfg):
    inv_set, noinv_set = _polarity_set(cfg), _polarity_noinv(cfg)
    declared = inv_set | noinv_set
    fails = []
    seen = set()
    for _c in sorted(inv_set & noinv_set):
        fails.append("metric %r is declared BOTH lower-is-better (`snapshot.inv`, or "
                     "`inv: true` on a `results` row) AND higher-is-better "
                     "(`snapshot.noinv`). The two cannot both be true, and the row is "
                     "rendered inverted while the polarity colour layer is told to skip it. "
                     "Delete whichever declaration is wrong." % _c)
    def look(name, where):
        c = _canon_metric(name)
        if not c or c in declared or c in seen: return
        if _reads_lower_is_better(name, _canon_metric):
            seen.add(c)
            fails.append("row %r in %s is not declared either way, and its name reads as though "
                         "LOWER might be better. An undeclared row colours by SIGN, so a rise "
                         "prints green. Say which it is: `inv: true` on the `results` row if "
                         "lower is better, or list the label in `snapshot.noinv` if higher is "
                         "better (a capital or coverage ratio, where a rise is good news). One "
                         "declaration covers every table." % (name, where))
    for sec in (cfg.get("results") or ()):
        for r in (sec.get("rows") or ()): look(r.get("name"), "the Results table")
    for r in ((cfg.get("kpiTable") or {}).get("rows") or ()): look(r.get("name"), "the KPI table")
    for r in ((cfg.get("snapshot") or {}).get("rows") or ()):
        look(_snap_cells(r)[0], "the snapshot")
    return fails

_UNSTABLE_LT = re.compile(r"^[A-Z]{2}_S_")

_POL_REG_PATH = os.path.join(HERE, "polarity_registry.json")
_POL_REG = None

def _polarity_registry():
    global _POL_REG
    if _POL_REG is None:
        try:
            with open(_POL_REG_PATH, encoding="utf-8") as fh:
                _POL_REG = (json.load(fh) or {}).get("codes") or {}
        except (OSError, ValueError) as e:
            sys.stderr.write("WARN polarity registry unreadable (%s); falling back to "
                             "declarations and name cues only.\n" % e)
            _POL_REG = {}
    return _POL_REG

def _registry_polarity(ltcode):
    e = _polarity_registry().get(str(ltcode or "").strip())
    return None if e is None else bool(e.get("lower_is_better"))

def _polarity_state(cfg, name, ltcode=None):
    c = _canon_metric(name)
    if c in (_polarity_set(cfg) | _polarity_noinv(cfg)):
        return "config"
    if _registry_polarity(ltcode) is not None:
        return "registry"
    return None

def _probe_ltcodes(cfg):
    return {p.get("name"): str(p.get("ltcode") or "")
            for p in ((cfg.get("consensusProbe") or {}).get("probed") or ())}

def _row_is_inverted(cfg, name, ltcode=None):
    c = _canon_metric(name)
    if c in _polarity_noinv(cfg): return False
    if c in _polarity_set(cfg):   return True
    r = _registry_polarity(ltcode)
    return bool(r) if r is not None else False

def _polarity_inventory(cfg):
    probe = _probe_ltcodes(cfg)
    warns, undeclared, seen = [], [], set()
    def look(name):
        c = _canon_metric(name)
        if not c or c in seen: return
        seen.add(c)
        lt = probe.get(name, "")
        if _polarity_state(cfg, name, lt) is not None: return
        undeclared.append(str(name))
        _c2 = _canon_metric(name)
        if any(k in _c2 for k in HIGHER_IS_BETTER_LABEL) and not _reads_lower_is_better(
                name, _canon_metric):
            return
        if lt:
            _why = ("a per-ticker `_S_` ltcode - the same code is a cost line on one issuer and "
                    "a profit line on another"
                    if _UNSTABLE_LT.match(lt) else
                    "an ordinary `_P_` code that the registry does not declare")
            warns.append("row %r is graded on %s, %s, and its direction is established nowhere - "
                         "not by the config, not by the registry - so it colours by SIGN and a "
                         "RISE prints green. If lower is better here, set `inv: true` on the "
                         "`results` row; if higher is better, either list the label in "
                         "`snapshot.noinv` or add the code to references/polarity_registry.json "
                         "to settle it for every future note."
                         % (name, lt, _why))
    for sec in (cfg.get("results") or ()):
        for r in (sec.get("rows") or ()): look(r.get("name"))
    for r in ((cfg.get("kpiTable") or {}).get("rows") or ()): look(r.get("name"))
    for r in ((cfg.get("snapshot") or {}).get("rows") or ()): look(_snap_cells(r)[0])
    inv_line = ""
    if undeclared:
        inv_line = ("%d of %d rows have no polarity from either the config or the registry "
                    "and colour by sign: %s. That is normal for a revenue or margin line and "
                    "wrong for a cost line. Nothing else in this run can tell them apart."
                    % (len(undeclared), len(seen), ", ".join(sorted(undeclared)[:8])
                       + (", ..." if len(undeclared) > 8 else "")))
    return warns, inv_line

def _delta_cell(v, arrow=True, inv=False):
    s=str(v).strip()
    if s=="": return ""
    up=s.startswith("+")
    dn=len(s)>1 and s[0]=="-" and s[1] in "0123456789$."
    if not (up or dn): return esc(s)
    tri=("&#8593; " if up else "&#8595; ") if arrow else ""
    good = (not up) if inv else up
    return '<span class="%s">%s%s</span>'%("up" if good else "dn", tri, esc(s))

def _change_cell(v, inv=False):
    s=str(v).strip()
    if s=="": return ""
    if s[0] in "+-" or s.startswith("&#8593;") or s.startswith("&#8595;"):
        return _delta_cell(s, True, inv)
    return '<span class="mid">'+esc(s)+'</span>'


def _snap_cells(row):
    if isinstance(row, dict):
        return (row.get("name", ""), row.get("actual", ""), row.get("note", ""),
                row.get("ref"), row)
    cells = list(row) + ["", "", ""]
    return (cells[0], cells[1], cells[2], cells[3] or None, None)

_SNAP_NUMERIC = re.compile(r"^\s*[+-]?\s*[\u00a3\u20ac\u00a5$]?\s*[\d,]+\.?\d*\s*(?:%|pp|bps|x|B|M|T|K)?\s*$")

def normalise_snapshot(cfg):
    snap = cfg.get("snapshot") or {}
    rows = snap.get("rows") or []
    results = {}
    for sec in (cfg.get("results") or ()):
        for r in (sec.get("rows") or ()):
            results[str(r.get("name"))] = r
    _dpm = results_dp_map(cfg)
    _shared = shared_results_dp(cfg)
    out = []
    for row in rows:
        label, actual, chg, ref, raw = _snap_cells(row)
        if raw is not None and raw.get("a") is not None:
            actual = fmtVal(raw.get("a"), raw.get("u"))
            if raw.get("py") is not None:
                _ra, _rp = raw.get("a"), raw.get("py")
                _sh = _shared.get(_ct_key(raw.get("name") or label))
                if _sh is not None and _sh[0] == raw.get("u") and _sh[1] is not None:
                    _ra, _rp = house_round(_ra, _sh[1]), house_round(_rp, _sh[1])
                chg = change(_ra, _rp, raw.get("u"))[0]
        elif ref:
            src = results.get(str(ref))
            if src is None:
                raise SystemExit("SNAPSHOT REF FAIL: snapshot row %r references Results row %r, "
                                 "which does not exist. The ref must name a row in `results`."
                                 % (label, ref))
            _sdp = _dpm.get(str(ref))
            _sq = dp_quantizer(_sdp)
            actual = fmtVal(_sq(src.get("a")), src.get("u"), _sdp)
            chg = change(_sq(src.get("a")), _sq(src.get("py")), src.get("u"), None, _sdp)[0]
        elif chg and _SNAP_NUMERIC.match(str(chg)):
            raise SystemExit(
                "SNAPSHOT-CONTRACT GATE FAIL: snapshot row %r types %r into the Change column. "
                "A delta in a table is never typed. Either give the row a 4th element naming "
                "its `results` row, or use the dict shape {name, a, py, u} so the renderer "
                "computes both cells from the same figures Section 4 uses. A literal is legal "
                "only for a non-numeric market note (\"Jul 30 session\", \"Sep 4 close\")."
                % (label, chg))
        out.append([label, actual, chg])
    return out

def eventSnapshot(snap, results=None, cfg=None):
    W=snap.get("w")
    if (not W) or len(W)!=3: W=[4400,2500,2500]
    head=('<tr><th class="l">Metric</th><th class="r">Actual</th>'
          '<th class="r">Change (Y/Y)</th></tr>')
    _inv = {str(x).strip().lower() for x in (snap.get("inv") or [])}
    body=""; z=_Zebra()
    for r in snap["rows"]:
        label,actual,chg=(list(r)+["",""])[:3]
        _base = re.sub(r"\s*\([^)]*\)\s*$", "", str(label)).strip().lower()
        mm=re.match(r"^(.*?)\s*(\([^)]*\))\s*$", str(label))
        lab=(esc(mm.group(1).rstrip())+' <span class="tag">'+esc(mm.group(2))+'</span>') if mm else esc(str(label))
        _ref = list(r)[3] if len(list(r)) > 3 and list(r)[3] else None
        _flip = _base in _inv
        if not _flip and cfg is not None:
            _nm = _ref or re.sub(r"\s*\([^)]*\)\s*$", "", str(label)).strip()
            _flip = _row_is_inverted(cfg, _nm, _probe_ltcodes(cfg).get(_nm))
        body+=(z.tr()+'<td class="l mname">'+lab+'</td>'
               '<td class="r">'+_delta_cell(actual,True)+'</td>'
               '<td class="r">'+_change_cell(chg, _flip)+'</td></tr>')
    return '<table class="data">'+colgroup(W)+head+body+'</table>'

_GROWTH_MARK = "&#8224;"

def growth_disclosure(cfg, seq_head="Q/Q"):
    if not [r for band in (cfg.get("results") or []) for r in (band.get("rows") or [])
            if isinstance(r, dict) and r.get("kind") == "growth"]:
        return ""
    return ("† already a year-over-year series: the Y/Y and %s columns show the change IN "
            "that rate, in pp, not a growth rate applied to it." % seq_head)

def resultsTable(sections, inv=(), seq_head="Q/Q", cfg=None, ltcodes=None):
    graded = True if cfg is None else (cfg.get("consensusColumn", True) is not False)
    if graded:
        cols=["Metric","Actual [A]","Consensus [E]","Y/Y",seq_head,"&Delta; vs Cons","Dir."]
        CW=[3060,1150,1200,940,900,1160,1430]
    else:
        cols=["Metric","Actual [A]","Y/Y",seq_head]
        CW=[4200,1800,1500,1340]
    ncol=len(cols)
    h="".join(f'<th class="{ "l" if i==0 else "r" }">{c}</th>' for i,c in enumerate(cols))
    body=""; z=_Zebra()
    _DPM = results_dp_map(cfg) if cfg is not None else {}
    for sec in sections:
        body+=f'<tr class="band"><td colspan="{ncol}">{esc(sec["band"])}</td></tr>'
        for r in sec["rows"]:
            _lt = (ltcodes or {}).get(r.get("name"), "")
            _inv = bool(r.get("inv")) or (_canon_metric(r.get("name")) in inv)
            if not _inv and cfg is not None:
                _inv = _row_is_inverted(cfg, r.get("name"), _lt)
            _u  = r.get("u")
            _dp = _DPM.get(r.get("name"))
            _q  = dp_quantizer(_dp)
            _a, _c   = _q(r.get("a")), _q(r.get("c"))
            _py, _pq = _q(r.get("py")), _q(r.get("pq"))
            dtxt,dsign=deltaCons(_a,_c,_u,_inv,_dp)
            dcol="up" if dsign>0 else ("dn" if dsign<0 else "mid")
            dirs=('<span class="up">&#8593;</span>' if dsign>0 else
                  ('<span class="dn">&#8595;</span>' if dsign<0 else '<span class="mid">&mdash;</span>'))
            _tr = z.tr()
            if _lt:
                _tr = _tr[:-1] + f' data-lt="{esc(_lt)}">' if _tr.endswith(">") else _tr
            if graded:
                body+=(_tr+
                  f'<td class="l">{esc(r["name"])}{_GROWTH_MARK if r.get("kind")=="growth" else ""}</td>'
                  f'<td class="r"><b>{fmtVal(_a,_u,_dp)}</b></td>'
                  f'<td class="r">{fmtVal(_c,_u,_dp)}</td>'
                  f'<td class="r sm">{yoy(_a,_py,_u)}</td>'
                  f'<td class="r sm">{yoy(_a,_pq,_u)}</td>'
                  f'<td class="r {dcol}"><b>{dtxt}</b></td>'
                  f'<td class="r">{dirs}</td></tr>')
            else:
                body+=(_tr+
                  f'<td class="l">{esc(r["name"])}{_GROWTH_MARK if r.get("kind")=="growth" else ""}</td>'
                  f'<td class="r"><b>{fmtVal(_a,_u,_dp)}</b></td>'
                  f'<td class="r sm">{yoy(_a,_py,_u)}</td>'
                  f'<td class="r sm">{yoy(_a,_pq,_u)}</td></tr>')
    return f'<table class="data">{colgroup(CW)}<tr>{h}</tr>{body}</table>'

_PERIOD_PREFIX = re.compile(r"^\s*(?:(?:FY|CY)?\d{2,4}|[1-4][QH]\d{2}|[12]H\d{2})\s+", re.I)

def _revision_inverted(cfg, r):
    if r.get("noinv"): return False
    if r.get("inv"): return True
    name = _PERIOD_PREFIX.sub("", str(r.get("name") or ""))
    if cfg is not None and (_canon_metric(name) in _polarity_noinv(cfg)): return False
    if cfg is not None and _row_is_inverted(cfg, name, _probe_ltcodes(cfg).get(name)): return True
    return bool(_reads_lower_is_better(name, _canon_metric))

def revisionsTable(rows, cfg=None):
    W=[3540,2100,2100,2100]; head=["Item","Before [E]","After [E]","Change"]
    h="".join(f'<th class="{ "l" if i==0 else "r" }">{esc(c)}</th>' for i,c in enumerate(head))
    body=""; z=_Zebra()
    for r in rows:
        up=(r["after"]-r["before"])>0
        if r.get("u")=="%":
            d=_xdiff(r["after"],r["before"]); chg=_spp(d)
        else:
            s=pct(r["after"],r["before"]); chg=_spct(s)
        good = up != _revision_inverted(cfg, r)
        arrow="&#8593;" if up else "&#8595;"; cls="up" if good else "dn"
        body+=(z.tr()+
          f'<td class="l">{esc(r["name"])}</td>'
          f'<td class="r">{fmtVal(r["before"],r.get("u"))}</td>'
          f'<td class="r"><b>{fmtVal(r["after"],r.get("u"))}</b></td>'
          f'<td class="r {cls}">{arrow} {chg}</td></tr>')
    return f'<table class="data">{colgroup(W)}<tr>{h}</tr>{body}</table>'

def takeaway(txt):
    return f'<div class="take"><b>Takeaway.&nbsp;&nbsp;</b>{md(txt)}</div>'

def consensus_gate(cfg):
    probe = cfg.get("consensusProbe")
    iss = []
    if not isinstance(probe, dict) or not probe.get("probed"):
        return ["no consensusProbe in the config. Run one batched pre-print get_estimates "
                "over EVERY ltcode that appears in `results`, then record each line as "
                "{name, ltcode, found} (found:null plus `evidence` when the series is truly absent)."]
    if not probe.get("asOf"):
        iss.append("consensusProbe.asOf is missing (the as-of date of the pre-print pull).")
    by_name = {}
    for p in probe["probed"]:
        if not p.get("name") or not p.get("ltcode"):
            iss.append("consensusProbe entry missing name or ltcode: %r" % (p,)); continue
        by_name[p["name"]] = p
    for sec in cfg.get("results", []):
        for r in sec.get("rows", []):
            nm = r.get("name"); p = by_name.get(nm)
            if p is None:
                iss.append("Results row %r was never probed for pre-print consensus. "
                           "Query its ltcode before publishing it ungraded." % nm)
                continue
            found, c = p.get("found"), r.get("c")
            if c is None:
                _b = p.get("brokers")
                if found is not None and isinstance(_b, int) and 0 <= _b < BROKER_FLOOR:
                    pass
                elif found is not None:
                    iss.append("Results row %r is ungraded (c:null) but the probe FOUND "
                               "consensus %s. Grade the row." % (nm, found))
                elif not str(p.get("evidence") or "").strip():
                    iss.append("Results row %r claims no consensus but records no `evidence`. "
                               "State the query window that came back empty." % nm)
            else:
                if found is None:
                    iss.append("Results row %r is graded against %s but the probe recorded no "
                               "consensus. A graded number must trace to the probe." % (nm, c))
                elif abs(float(found) - float(c)) > max(0.005, abs(float(c)) * 0.001):
                    iss.append("Results row %r consensus %s does not match the probed value %s."
                               % (nm, c, found))
    return iss

GATE_MODE = os.environ.get("INSYNC_GATE_MODE", "strict").strip().lower()

GATE_NAMES = ("kpi_gate", "consensus_gate", "spine_gate", "sector_gate", "broker_count",
              "table_contract", "valuation_sanity", "price_basis", "note_contract",
              "relevance", "one_currency", "polarity", "identity", "snapshot_contract",
              "unit", "palette", "page-fit", "validation", "graded_column",
              "table_figures", "staleness", "kpi_nesting", "broker_disclosure")

GATE_MODE_SCOPE = ("spine_gate", "sector_gate", "broker_count", "table_contract",
                   "valuation_sanity", "price_basis", "note_contract", "relevance",
                   "one_currency", "polarity", "identity", "kpi_nesting")

def unit_contract(cfg):
    fails = []
    def ok(u):
        if u in UNIT_VOCABULARY: return True
        m = _UNIT_RE.match(str(u or ""))
        return bool(m and (m.group(1) or m.group(2)))
    def look(u, name, where, vals):
        if u in RETIRED_UNITS:
            repl, why = RETIRED_UNITS[u]
            fails.append("row %r in %s carries unit %r, retired in v7.7.0. Use %r: %s."
                         % (name, where, u, repl, why))
            return
        if not ok(u):
            fails.append("row %r in %s carries unit %r, which is not in the vocabulary. The "
                         "figure would print as a bare number with no currency and no magnitude. "
                         "Unit tokens are CASE SENSITIVE: the magnitude is B, T or M in upper "
                         "case, so '$b' is not '$B'. Legal tokens: %s."
                         % (name, where, u, ", ".join(sorted(UNIT_VOCABULARY))))
            return
        if u == "#":
            bad = [v for v in vals if v is not None and abs(v - house_round(v, 0)) > 1e-9]
            if bad:
                fails.append("row %r in %s uses u='#' (a count in WHOLE units) for %r. Printed as "
                             "a whole number the magnitude is lost - 16.721 becomes '17', and two "
                             "cells that differ print identically. Use '#M' or '#K'."
                             % (name, where, bad[0]))
    for sec in (cfg.get("results") or ()):
        for r in (sec.get("rows") or ()):
            look(r.get("u"), r.get("name"), "the Results table",
                 [r.get("a"), r.get("c"), r.get("py"), r.get("pq")])
    for r in ((cfg.get("kpiTable") or {}).get("rows") or ()):
        if r.get("kind") == "growth": continue
        look(r.get("u", "2d"), r.get("name"), "the KPI table", list(r.get("vals") or ()))
    for r in ((cfg.get("snapshot") or {}).get("rows") or ()):
        if isinstance(r, dict) and r.get("a") is not None:
            look(r.get("u"), r.get("name"), "the snapshot", [r.get("a"), r.get("py")])
    return fails

_EXACT_MATCH_TYPES = ("exact_ticker", "exact_company", "exact_isin", "exact_cusip")

def _period_key(q):
    s = _norm_period(q)
    m = re.match(r"(\d)Q(\d\d)$", s)
    if m: return (2000 + int(m.group(2)), int(m.group(1)))
    m = re.match(r"(\d)H(\d\d)$", s, re.I)
    if m: return (2000 + int(m.group(2)), 2 * int(m.group(1)))
    m = re.match(r"(\d{4})$", s)
    if m: return (int(m.group(1)), 4)
    return None

def identity_contract(cfg):
    fails = []
    ip = cfg.get("identityProbe")
    if not isinstance(ip, dict) or not ip:
        return ["no identityProbe in the config. Record what `search_tickerid` actually "
                "returned - {ticker, company, tickerId, matchType, latestFiscalQuarter} - "
                "before any other pull. A note whose every number is internally consistent "
                "and whose masthead names the wrong issuer passes every other gate here."]
    mt = str(ip.get("matchType") or "").strip().lower()
    if not mt:
        fails.append("identityProbe records no matchType. A similarity SCORE is not a "
                     "discriminator: 98.5, 98.83 and 99.14 have all returned a different "
                     "company at status success. Record the match type the resolver reported.")
    elif mt not in _EXACT_MATCH_TYPES:
        fails.append("identityProbe.matchType is %r. Only an exact identifier match may be "
                     "wrapped (%s). A fuzzy or partial match must be resolved with the user, "
                     "not guessed - the MCP's own resolver says so." % (mt, ", ".join(_EXACT_MATCH_TYPES)))
    for key, cfgkey, what in (("ticker", "ticker", "ticker"), ("company", "company", "company name")):
        want, got = str(cfg.get(cfgkey) or "").strip(), str(ip.get(key) or "").strip()
        if not got:
            fails.append("identityProbe records no %s." % what)
        elif want and got.lower() != want.lower():
            fails.append("the masthead %s is %r and identityProbe recorded %r. The note names "
                         "an issuer the resolver did not return." % (what, want, got))
    return fails

def _enforce(label, ticker, fails, scope=None):
    if not fails:
        return False
    body = "\n  - ".join(fails)
    if GATE_MODE == "warn" and scope in GATE_MODE_SCOPE:
        sys.stderr.write("WARN %s %s (INSYNC_GATE_MODE=warn, not blocking):\n  - %s\n"
                         % (label, ticker, body))
        return False
    raise SystemExit("%s FAIL %s:\n  - %s\n\n(Set INSYNC_GATE_MODE=warn to downgrade the "
                     "config-shape gates - %s - to warnings while migrating a saved config. "
                     "It cannot downgrade a validation layer, nor the consensus, KPI, unit, "
                     "palette or page-fit gate.)"
                     % (label, ticker, body, ", ".join(GATE_MODE_SCOPE)))

_SECTOR_RESOLUTIONS = ("table", "live", "default")
_GUIDANCE_SOURCES   = ("get_guidance", "table")

def sector_gate(cfg):
    sp = cfg.get("sectorProfile")
    f, w = [], []
    if not isinstance(sp, dict) or not sp:
        return (["no sectorProfile in the config. Resolve Step 0 decisions 2 to 4 for this "
                 "issuer (subindustry from search_tickerid, the sector row, the primary margin "
                 "ltcode proved with get_actuals, the guidance variable read from "
                 "get_guidance(latest=true)) and record them. A missing audit trail means the "
                 "step was not run."], w)

    if not str(sp.get("subindustry") or "").strip():
        f.append("sectorProfile.subindustry is missing. Take it from search_tickerid "
                 "(`insync_subindustry`); it is what selects the sector spine.")

    res = sp.get("resolution")
    if res not in _SECTOR_RESOLUTIONS:
        f.append("sectorProfile.resolution is %r; expected one of %s. 'table' = a named row in "
                 "kpi-selection-standard matched, 'live' = the spine was proved entirely from "
                 "MCP probes, 'default' = no row matched."
                 % (res, " / ".join(_SECTOR_RESOLUTIONS)))

    lt = str(sp.get("marginLtcode") or "").strip()
    if not lt:
        f.append("sectorProfile.marginLtcode is missing. Name the ltcode behind the Event "
                 "Snapshot margin row.")
    elif sp.get("marginProved") is not True:
        f.append("sectorProfile.marginLtcode %s is not proved (marginProved is not true). Fire "
                 "it with get_actuals for the printed quarter; if it returns nothing, walk the "
                 "ladder IS_P_114-M -> IS_P_110-M -> IS_P_106-M and log the swap in "
                 "sectorProfile.substitutions." % lt)

    gl, gs = sp.get("guidanceLive"), sp.get("guidanceSource")
    if not isinstance(gl, bool):
        f.append("sectorProfile.guidanceLive is missing. Call get_guidance(ticker_id, "
                 "latest=true) and record true when it returned rows, false when it did not. "
                 "The call is not optional: it is what decides decision 3.")
    if gs not in _GUIDANCE_SOURCES:
        f.append("sectorProfile.guidanceSource is %r; expected 'get_guidance' or 'table'."
                 % (gs,))
    elif gs == "table" and gl is True:
        f.append("sectorProfile.guidanceSource is 'table' although get_guidance returned rows "
                 "for this issuer. The live answer wins: lead the Guidance section on a metric "
                 "the company actually guides, not one the sector row prescribes.")
    elif gs == "get_guidance" and gl is False:
        f.append("sectorProfile.guidanceSource is 'get_guidance' but guidanceLive is false. "
                 "One of the two is wrong; a guidance variable cannot come from a call that "
                 "returned nothing.")
    if gs in _GUIDANCE_SOURCES and not str(sp.get("guidanceVariable") or "").strip():
        f.append("sectorProfile.guidanceVariable is missing. It leads Section 7.")

    for s in sp.get("substitutions") or []:
        if not (isinstance(s, dict) and s.get("tried") and s.get("used")
                and str(s.get("reason") or "").strip()):
            f.append("sectorProfile.substitutions entry needs tried, used and reason: %r" % (s,))

    if res == "default":
        w.append("no sector row matched %r, so the generic default spine (operating margin "
                 "ex-1x, revenue and EPS) is in use. Check whether this subindustry deserves a "
                 "row in references/kpi-selection-standard.md."
                 % (str(sp.get("subindustry") or "this subindustry")))
    return f, w


def sector_disclosure(cfg):
    sp = cfg.get("sectorProfile") or {}
    bits = []
    if sp.get("resolution") == "default":
        bits.append("No sector spine matched %s, so generic revenue and EPS framing is used."
                    % (str(sp.get("subindustry") or "this subindustry")))
    for s in sp.get("substitutions") or []:
        bits.append("Primary margin %s substituted for %s (%s)."
                    % (s.get("used"), s.get("tried"), str(s.get("reason")).rstrip(".")))
    return " ".join(bits)


def _kpi_cap(cfg):
    base = str(cfg.get("kpiCap") or "").strip()
    d = sector_disclosure(cfg)
    if not d or d in base:
        return base
    return (base + " " + d).strip()


_SPINE_REVENUEISH = ("revenue", "sales", "turnover", "net interest income", "interest income",
                     "interest and dividend income", "investment income", "fee income",
                     "commission income", "rental", "rent income", "total income", "premium",
                     "ffo", "affo", "billings", "gross written", "gross bookings")
_SPINE_WRONG = ("net income", "eps", "earnings per share", "margin", "total assets",
                "total equity", "book value", "shareholders equity", "net asset value",
                "cash flow", "profit", "ebitda", "ebit", "pre-tax", "pretax",
                "shares outstanding", "dividend per share")

def spine_gate(cfg):
    sp = cfg.get("spineProbe")
    f, w = [], []
    if not isinstance(sp, dict) or not sp.get("revenue"):
        return (["no spineProbe in the config. Record what get_actuals actually RETURNED for the "
                 "revenue ltcode - {ltcode, lineitem, yoyNonNull} - so a mis-mapped code cannot "
                 "print a wrong number on a right-looking page. VERIFIED on JIOFIN IN, where "
                 "IS_P_101 returns 'Total Net Income'."], w)
    r = sp["revenue"]
    li = str(r.get("lineitem") or "").strip()
    lt = str(r.get("ltcode") or "").strip()
    if not li or not lt:
        f.append("spineProbe.revenue needs both ltcode and lineitem, copied verbatim from the "
                 "get_actuals payload.")
        return f, w
    low = li.lower()
    if not any(t in low for t in _SPINE_REVENUEISH):
        hit = next((t for t in _SPINE_WRONG if t in low), None)
        accepted = r.get("accepted") is True
        reason = str(r.get("acceptedReason") or "").strip()
        if hit and accepted:
            f.append("spineProbe.revenue: ltcode %s returns LINEITEM %r, which names %r. That "
                     "cannot be overridden: a %s line is not a top line for any issuer. Find the "
                     "real revenue series with search_lineitem." % (lt, li, hit, hit))
        elif accepted and reason:
            w.append("spineProbe.revenue: ltcode %s returns LINEITEM %r, accepted by explicit "
                     "override: %s" % (lt, li, reason))
        elif hit:
            f.append("spineProbe.revenue: ltcode %s returns LINEITEM %r, which names %r, not a "
                     "revenue line. The code is mis-mapped for this issuer: find the real revenue "
                     "series with search_lineitem before rendering." % (lt, li, hit))
        else:
            f.append("spineProbe.revenue: ltcode %s returns LINEITEM %r, which does not read as a "
                     "revenue line. If this issuer's top line genuinely is that, set "
                     "spineProbe.revenue.accepted true with an acceptedReason saying why; "
                     "otherwise find the real revenue series with search_lineitem."
                     % (lt, li))
    if r.get("yoyNonNull") is not True:
        f.append("spineProbe.revenue: get_actuals returned no Y/Y anywhere in the window for %s. "
                 "A top-line series with no growth at all is the mis-mapping signature, and the "
                 "Change column cannot be built from it." % lt)
    return f, w



def ungrade_thin_panels(cfg):
    probe = {p.get("name"): p for p in ((cfg.get("consensusProbe") or {}).get("probed") or [])
             if isinstance(p, dict)}
    names, bands = [], []
    for sec in cfg.get("results", []) or []:
        rows = []
        for r in sec.get("rows", []) or []:
            b = (probe.get(r.get("name")) or {}).get("brokers")
            if (isinstance(r, dict) and r.get("c") is not None and isinstance(b, int)
                    and 0 <= b < BROKER_FLOOR):
                r = dict(r, c=None)
                names.append(r.get("name"))
            rows.append(r)
        bands.append(dict(sec, rows=rows))
    if not names:
        return cfg, []
    return dict(cfg, results=bands, _thinPanelRows=names), names


def thin_panel_disclosure(cfg):
    names = cfg.get("_thinPanelRows") or []
    if not names:
        return ""
    return ("No usable pre-print consensus for %s, so %s ungraded."
            % (", ".join(names), "that row is" if len(names) == 1 else "those rows are"))


def broker_check(cfg):
    probe = cfg.get("consensusProbe") or {}
    f, w, thin = [], [], []
    graded = set()
    for sec in cfg.get("results", []):
        for r in sec.get("rows", []):
            if r.get("c") is not None:
                graded.add(r.get("name"))
    for p in probe.get("probed", []) or []:
        nm = p.get("name")
        if nm not in graded:
            continue
        b = p.get("brokers")
        if not isinstance(b, int):
            f.append("consensusProbe entry %r is graded but carries no `brokers` count. "
                     "get_estimates returns BROKERCOUNT; record it, because a one-broker mean "
                     "and an eighteen-broker mean are not the same claim." % nm)
        elif b < 0:
            f.append("consensusProbe entry %r reports %r brokers. A broker count cannot be "
                     "negative - re-read BROKERCOUNT from get_estimates." % (nm, b))
        elif b < BROKER_FLOOR:
            thin.append((nm, b))
            f.append(
                "consensusProbe entry %r is graded against a panel of %d, below the minimum "
                "panel of %d. Until v7.11.0 this shipped with a disclosure line in the Section "
                "4 caption naming the count. The firm cannot disclose broker counts, that line "
                "has been removed, and a caveat the reader never sees is not a caveat: the note "
                "cannot grade a print against a panel that thin now that the disclosure route "
                "is gone. Two ways out, and note that simply setting `c: null` is NOT one of "
                "them - `consensus_gate` blocks an ungraded row whose probe found a value. "
                "Either REMOVE %r from `results` altogether (and the snapshot `ref` that points "
                "at it), carrying the line in the KPI table instead; or set "
                "`consensusColumn: false` and grade the note against the guidance midpoint, "
                "naming that in `consensusBasis`. Do NOT write the count onto the page: it "
                "stays in consensusProbe, which is internal."
                % (nm, b, BROKER_FLOOR, nm))
    return f, w, thin


def broker_disclosure(thin):
    return ""


_BROKER_WORD = re.compile(r"(?i)\bbrokers?\b")

_BROKER_EXEMPT_BLOCKS = frozenset((
    "consensusProbe", "guidanceConsensusProbe", "spineProbe", "identityProbe",
    "priceBasis", "guidanceFreshness", "documentAttempts", "sectorProfile",
    "kpiUniverse", "kpiProvenance", "kpiDiscovery", "kpiSubstitutions",
    "revisionsScope", "guidanceBasis", "consensusBasis", "valFxBasis",
))


def _prose_strings(node, path="", out=None):
    if out is None:
        out = []
    if isinstance(node, dict):
        for k, v in node.items():
            if str(k).startswith("_"):
                continue
            if not path and k in _BROKER_EXEMPT_BLOCKS:
                continue
            _prose_strings(v, "%s.%s" % (path, k) if path else str(k), out)
    elif isinstance(node, (list, tuple)):
        for i, v in enumerate(node):
            _prose_strings(v, "%s[%d]" % (path, i), out)
    elif isinstance(node, str):
        out.append((path or "<root>", node))
    return out


def broker_prose_gate(cfg):
    label = str(((cfg.get("sectorProfile") or {}).get("subindustry")) or "").strip()

    def _hits(text):
        s = str(text)
        if label:
            s = re.sub(re.escape(label), " ", s, flags=re.I)
        return _BROKER_WORD.findall(s)

    def _excerpt(text):
        m = _BROKER_WORD.search(str(text))
        if not m:
            return str(text)[:80]
        a, b = max(0, m.start() - 45), min(len(str(text)), m.end() + 45)
        return ("..." if a else "") + str(text)[a:b] + ("..." if b < len(str(text)) else "")

    fails = []
    seen, said = [], set()
    for path, text in _prose_strings(cfg):
        if _hits(text):
            seen.append((path, text))
            said.add(str(text).strip())
    for where, text in (("the Section 4 caption (resultsCap plus forced disclosures)",
                         _results_cap(cfg)),
                        ("the Section 6 caption (kpiCap plus the sector disclosure)",
                         _kpi_cap(cfg)),
                        ("the Section 7 guidance caption (guidanceBasis plus guidanceTableCap)",
                         (GUIDANCE_BASIS.get(str(cfg.get("guidanceBasis") or "").strip(), "")
                          + " " + str(cfg.get("guidanceTableCap") or "")))):
        if _hits(text) and not any(s and s in str(text) for s in said):
            seen.append((where, text))
    for where, text in seen:
        fails.append(
            "BROKER COUNT ON THE PAGE: %s says %r. The number of contributing brokers is "
            "INTERNAL - it belongs in `consensusProbe`, which is where `broker_check()` reads "
            "it and where a reviewer can find it - and it is never written into a caption, a "
            "takeaway, a note, a bullet or a table cell. Until v7.11.0 the renderer wrote it "
            "there itself; that line is gone and this lint stops it coming back by hand. "
            "Rewrite the sentence without naming the panel, and if the panel is too thin to "
            "grade against, `broker_check()` will say so separately."
            % (where, _excerpt(text)))
    return fails


BASIS_BAND_PCT = 40.0

def basis_band_rows(cfg):
    out = []
    dpm = results_dp_map(cfg)
    for sec in (cfg.get("results") or ()):
        for r in (sec.get("rows") or ()):
            a, c, u = r.get("a"), r.get("c"), str(r.get("u") or "")
            if a is None or c is None or not c: continue
            if u.startswith("%"): continue
            if (a < 0) != (c < 0): continue
            _q = dp_quantizer(dpm.get(r.get("name")))
            a, c = _q(a), _q(c)
            if not c: continue
            sp = (a - c) / abs(c) * 100.0
            if abs(sp) > BASIS_BAND_PCT:
                out.append((str(r.get("name")), sp))
    return out

def basis_disclosure(rows):
    if not rows:
        return ""
    names = ", ".join("%s (%+.0f%%)" % (n, s) for n, s in rows)
    return ("Basis note: %s printed a surprise beyond %.0f%%, a size usually driven by "
            "definition rather than result; confirm both sides sit on the same basis before "
            "reading the delta as a beat or a miss." % (names, BASIS_BAND_PCT))

def _results_cap(cfg):
    base = str(cfg.get("resultsCap") or "").strip()
    for d in (thin_panel_disclosure(cfg),
              basis_disclosure(basis_band_rows(cfg)),
              widened_disclosure(widened_rows(cfg)),
              growth_disclosure(cfg, _seq_head(cfg.get("quarter")))):
        if d and d not in base:
            base = (base + " " + d).strip()
    return base


_GEO_BOLD_EM = {}
for _w, _cs in (
    (0.2539, ' '),   (0.2690, "'"),  (0.3281, ',.'), (0.3442, 'l'),  (0.3462, 'j'),
    (0.3535, 'i'),   (0.3672, ':;'), (0.3765, '!'),  (0.3789, '-'),  (0.3877, '|'),
    (0.3931, 'f'),   (0.3975, 't'),  (0.4458, 'I'),  (0.4468, '()[]'),
    (0.4717, '/\\'), (0.4819, '*'),  (0.4897, '1'),  (0.5000, '`{}'),
    (0.5098, '"'),   (0.5127, 's'),  (0.5200, 'r'),  (0.5254, 'z'),  (0.5312, 'c'),
    (0.5483, '?'),   (0.5542, '7'),  (0.5620, 'y'),  (0.5669, 'v'),  (0.5718, 'e'),
    (0.5767, 'g'),   (0.5879, 'x'),  (0.5952, 'J'),  (0.5957, 'a'),  (0.5991, '5'),
    (0.6245, '3'),   (0.6265, '2'),  (0.6318, 'k'),  (0.6357, 'o'),  (0.6406, '$'),
    (0.6455, 'b'),   (0.6479, '69'), (0.6484, 'q'),  (0.6489, 'S'),  (0.6494, '4'),
    (0.6577, 'p'),   (0.6631, 'd'),  (0.6714, 'F'),  (0.6763, '8'),  (0.6768, 'u'),
    (0.6797, 'h'),   (0.6841, 'T'),  (0.6855, 'L'),  (0.6895, 'Z'),  (0.6899, 'n'),
    (0.7012, '0P'),  (0.7031, '#+<=>^_~'),           (0.7153, 'C'),  (0.7212, 'E'),
    (0.7319, 'Y'),   (0.7573, 'B'),  (0.7583, 'A'),  (0.7622, 'V'),  (0.7974, 'R'),
    (0.7993, '&'),   (0.8071, 'G'),  (0.8086, 'X'),  (0.8169, 'K'),  (0.8198, 'OQ'),
    (0.8335, 'U'),   (0.8340, 'D'),  (0.8394, 'N'),  (0.8633, 'w'),  (0.8794, '%'),
    (0.9131, 'H'),   (0.9668, '@'),  (1.0156, 'm'),  (1.0234, 'M'),  (1.1265, 'W')):
    for _c in _cs: _GEO_BOLD_EM[_c] = _w
_GEO_EM_DEFAULT = 0.66

_CO_TRIM   = 0.00
_MARK_TRIM = 0.00

_SUBSTITUTE_WIDTH_FACTOR = 1.12


def _resolve_face_scale():
    forced = os.environ.get("INSYNC_FACE_WIDTHS")
    if forced == "georgia":
        return 1.0
    if forced == "substitute":
        return _SUBSTITUTE_WIDTH_FACTOR
    want, accepted = "Georgia", ["Georgia"]
    try:
        here = os.path.dirname(os.path.abspath(__file__))
        for up in range(1, 5):
            cand = os.path.normpath(os.path.join(here, *([".."] * up), "common", "brand.json"))
            if not os.path.exists(cand):
                continue
            with open(cand, encoding="utf-8") as fh:
                m = json.load(fh).get("masthead", {})
            product = os.path.basename(os.path.dirname(os.path.dirname(cand)))
            want = m.get("face_overrides", {}).get(product) or m.get("face", "Georgia")
            accepted = [want] + list(m.get("face_accepted_substitutes", {}).get(want, []))
            break
    except Exception:
        pass
    try:
        got = subprocess.run(["fc-match", "-f", "%{family}", want + ":bold"],
                             capture_output=True, text=True, timeout=10).stdout.strip()
    except Exception:
        return 1.0
    if not got:
        return 1.0
    flat = got.replace(" ", "").lower()
    if any(a.replace(" ", "").lower() in flat for a in accepted):
        return 1.0
    sys.stderr.write(
        "[masthead] %r is NOT installed on this box - fontconfig resolves it to %r, which is "
        "not a metric-faithful stand-in. Sizing the company line for the substitute so it "
        "cannot wrap. Install %s to render at the intended size.\n" % (want, got, want))
    return _SUBSTITUTE_WIDTH_FACTOR


_FACE_SCALE = _resolve_face_scale()


def _tw(s, pt):
    return sum(_GEO_BOLD_EM.get(c, _GEO_EM_DEFAULT) for c in s) * pt * _FACE_SCALE

def _fits(line, pt, budget):
    return _tw(line, pt) <= budget * 0.97

def _balanced_split(text, pt, cap):
    words = text.split(' ')
    best = None
    for i in range(1, len(words)):
        a, b = ' '.join(words[:i]), ' '.join(words[i:])
        wa, wb = _tw(a, pt), _tw(b, pt)
        if wa > cap or wb > cap: continue
        sc = abs(wa - wb)
        if best is None or sc < best[0]: best = (sc, a, b)
    if best: return best[1], best[2]
    acc = []
    while words and _tw(' '.join(acc + [words[0]]), pt) <= cap: acc.append(words.pop(0))
    if not acc: acc.append(words.pop(0))
    return ' '.join(acc), (' '.join(words) or ' ')

def _co_html(line, tk):
    i = line.find(tk)
    if i < 0: return esc(line)
    return (esc(line[:i]) + '<span class="tk">' + esc(tk) + '</span>'
            + esc(line[i + len(tk):]))

CSS = """
@page{size:letter;margin:0.5in 0.5in 0.5in;
 @bottom-left{content:"__FIRM__  |  __FOOT__";font-family:Calibri,Carlito,Arial,sans-serif;font-size:6.5pt;color:__FOOTC__}
 @bottom-right{content:"Page " counter(page) " of " counter(pages);font-family:Calibri,Carlito,Arial,sans-serif;font-size:6.5pt;color:__FOOTC__}}
*{box-sizing:border-box;-webkit-print-color-adjust:exact;print-color-adjust:exact;color-adjust:exact}
html,body{-webkit-print-color-adjust:exact;print-color-adjust:exact}
body{margin:0;font-family:Calibri,Carlito,Arial,sans-serif;color:__INK__;font-size:9.7pt;line-height:1.28}
.mast{width:100%;border:1px solid __BOX__;border-collapse:collapse;table-layout:fixed}
.mast td{vertical-align:bottom;padding:4px 7px}
.mast .logo{width:10.0%;text-align:left}
.mast .logo img{width:59px;height:46px;display:block}
.mast .mid{width:55.24%}
.mast .rt{width:34.76%;text-align:right;vertical-align:top}
.brand{font-family:Georgia,Gelasio,"Liberation Serif",serif;font-size:13pt;margin:0}
.brand .i{color:__ORANGE__;font-weight:bold} .brand .a{color:__DARK__}
.co{font-family:Georgia,Gelasio,"Liberation Serif",serif;font-weight:bold;font-size:13pt;color:__DARK__;margin:0;white-space:nowrap}
.co .tk{color:__ORANGE__} .cosm{font-size:12pt}
.ew{font-family:Georgia,Gelasio,"Liberation Serif",serif;font-weight:bold;font-size:18pt;color:__DARK__;line-height:1.05}
.pr{font-family:Georgia,Gelasio,"Liberation Serif",serif;font-size:9pt;color:__GRY__} .pr b{color:__ORANGE__}
.orule{border-bottom:1.75pt solid __ORANGE__;margin:3px 0 7px;height:0}
.ex{background:__EXBG__;border-left:2.5pt solid __ORANGE__;padding:6px 10px 7px 11px;margin:0 0 3px}
.ex h3{font-size:9pt;color:__DARK__;margin:0 0 4px;font-weight:bold}
.ex .res{font-size:8pt;margin:0 0 4px}
.ex .res b{color:__DARK__}
.exlabel{font-size:7pt;color:__LABEL__;font-weight:bold;letter-spacing:.3px;margin:5px 0 2px}
h2.sec{font-family:Calibri,Carlito,Arial,sans-serif;font-size:12.5pt;font-weight:bold;color:__DARK__;margin:9px 0 4px;padding-bottom:2px;border-bottom:1.5pt solid __ORANGE__}
/* --- keep-together: no split tables, no orphaned section headers --- */
h2.sec{break-after:avoid;page-break-after:avoid}
.sub{break-after:avoid;page-break-after:avoid}
table.data,tr,.ex,.take,.mast{break-inside:avoid;page-break-inside:avoid}
table.data thead{display:table-header-group}
.cap{break-before:avoid;page-break-before:avoid}
h2.sec+*,.sub+*{break-before:avoid}
h2.sec .sn{color:__ORANGE__;margin-right:9px}
.sub{font-size:8.5pt;font-weight:bold;color:__GREY__;letter-spacing:.4px;margin:6px 0 2px}
.cap{font-size:6.8pt;color:__GREY__;font-style:italic;margin:3px 0 5px}
ul.r{margin:2px 0 4px;padding-left:15px} ul.r li{font-size:9.3pt;line-height:1.3;margin:2px 0}
ul.r li::marker{color:__ORANGE__}
.keep{break-inside:avoid;page-break-inside:avoid}
.mflab{font-weight:600;color:__LABEL__}
.take{background:__TAKE__;border-left:2.75pt solid __ORANGE__;padding:5px 10px;margin:4px 0 3px;font-size:9.3pt;color:__INK__}
.take b{color:__DARK__}
table.data{border-collapse:collapse;width:100%;margin:3px 0 5px;table-layout:fixed}
table.data th{background:__HEADBG__;color:__DARK__;font-weight:bold;font-size:7pt;padding:4.2px 8px;border:0;border-bottom:.7pt solid __HRULE__;vertical-align:bottom}
table.data td{font-size:8.3pt;padding:3.6px 8px;border:0;border-bottom:.25pt solid __RRULE__;font-variant-numeric:tabular-nums;word-wrap:break-word;color:__INK__}
table.data th.l,table.data td.l{text-align:left} table.data th.r,table.data td.r{text-align:right}
table.data td.mname{color:__DARK__;font-weight:600}
.tag{font-size:6.3pt;color:__GREY__;font-weight:normal;letter-spacing:0}
table.data td.sm{font-size:8pt} tr.band td{background:__BAND__;font-weight:bold;font-size:7.4pt;color:__DARK__}
/* Zebra: stamped by _Zebra over BODY rows only, so a band never takes a stripe. */
table.data tr.z td{background:__ZEBRA__}
.up{color:__GREEN__;font-weight:bold} .dn{color:__RED__;font-weight:bold} .mid{color:__GREY__}
.src{margin-top:8px;border-top:.5pt solid __GRID__;padding-top:5px;font-size:6.5pt;color:__GREY__;line-height:1.35}
"""

def _result_color(cfg):
    v = str(cfg.get("resultColor", "") or "").strip()
    if not v:
        return DARK
    if v.upper() in PALETTE:
        return PALETTE[v.upper()]
    if v.upper() in {h.upper() for h in PALETTE.values()}:
        return v
    sys.stderr.write('WARN resultColor %r is not in the house palette; using ink. Use a key '
                     '(GREEN / RED / DARK / LABEL / GREY) or a palette hex.\n' % v)
    return DARK

_ADV_MONEY = re.compile(r"[\$£€¥]\s?([0-9][0-9,]*\.?[0-9]*)\s?(B|M|K|T)\b")

_ADV_PCT = re.compile(r"([0-9][0-9,]*\.?[0-9]*)\s?(pp|%)")
_ADV_CUE = re.compile(r"(?i)\bY/Y\b|\bQ/Q\b|\bH/H\b|sequential|year[- ]over[- ]year|"
                      r"half[- ]over[- ]half|"
                      r"\b(beat|beats|missed|misses|topped|trailed)\b.{0,40}?\bby\b")
_ADV_PCT_NEAR_PP = 2.0
_ADV_PCT_NEAR_REL = 0.10

def _adv_sentences(text):
    return re.split(r"(?<=[.!?])\s+", text)

def _prose_only(html):
    x = re.sub(r"<style[^>]*>.*?</style>", " ", html, flags=re.S | re.I)
    x = re.sub(r"<script[^>]*>.*?</script>", " ", x, flags=re.S | re.I)
    x = re.sub(r"<table[^>]*>.*?</table>", " ", x, flags=re.S)
    return re.sub(r"<[^>]+>", " ", x)

def prose_percentage_advisory(html):
    tables = re.findall(r"<table[^>]*>.*?</table>", html, re.S)
    pool = set()
    for t in tables:
        flat = re.sub(r"<[^>]+>", " ", t)
        for m in _ADV_PCT.finditer(flat):
            try: pool.add(float(m.group(1).replace(",", "")))
            except ValueError: pass
    if not pool:
        return []
    prose = _prose_only(html)
    out = []
    for sent in _adv_sentences(prose):
        if not _ADV_CUE.search(sent):
            continue
        for m in _ADV_PCT.finditer(sent):
            try: v = float(m.group(1).replace(",", ""))
            except ValueError: continue
            if re.match(r"\s*of\b", sent[m.end():m.end() + 24], re.I):
                continue
            near = min(pool, key=lambda p: abs(p - v))
            if abs(near - v) <= _ADV_PCT_NEAR_PP: continue
            if near and abs(near - v) / abs(near) <= _ADV_PCT_NEAR_REL: continue
            out.append((m.group(0).strip(), "%g%s" % (near, m.group(2))))
    return sorted(set(out))

def prose_figure_advisory(html):
    tables = re.findall(r"<table[^>]*>.*?</table>", html, re.S)
    anywhere = set()
    for t in tables:
        for m in _ADV_MONEY.finditer(re.sub(r"<[^>]+>", " ", t)):
            anywhere.add((float(m.group(1).replace(",", "")), m.group(2)))
    printed = set()
    for t in tables:
        rows = re.findall(r"<tr[^>]*>(.*?)</tr>", t, re.S)
        if not rows: continue
        heads = re.findall(r"<th[^>]*>(.*?)</th>", rows[0], re.S)
        ac = next((i for i, h in enumerate(heads)
                   if re.search(r"actual", re.sub(r"<[^>]+>", "", h), re.I)), None)
        if ac is None: continue
        for r in rows[1:]:
            cells = re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", r, re.S)
            if len(cells) > ac:
                for m in _ADV_MONEY.finditer(re.sub(r"<[^>]+>", "", cells[ac])):
                    printed.add((float(m.group(1).replace(",", "")), m.group(2)))
    prose = _prose_only(html)
    out = []
    for m in _ADV_MONEY.finditer(prose):
        v, mag = float(m.group(1).replace(",", "")), m.group(2)
        if (v, mag) in anywhere:
            continue
        for pv, pmag in printed:
            if pmag == mag and pv and v != pv and abs(v - pv) / abs(pv) < 0.01:
                out.append((m.group(0).strip(), "%s%s" % (pv, pmag)))
    return sorted(set(out))

GUIDANCE_HEAD_LEGACY = ["Guide Metric", "Prior [G]", "New [G]", "Consensus [E]"]
GUIDANCE_HEAD = ["Guide Metric", "Prior [G]", "New [G]", "Pre-print [E]", "Post-print [E]"]
FORWARD_HEAD = ["Forward Metric", "Consensus [E]", "Basis"]

def table_contract(cfg):
    fails = []
    gt = cfg.get("guidanceTable")
    if gt:
        head = [str(h).strip() for h in (gt.get("head") or [])]
        if head != GUIDANCE_HEAD:
            fails.append("guidanceTable head is %r; the contract is %r. Prior [G] is the guide "
                         "the company had given FOR the printed quarter (the bar it set), New [G] "
                         "is the guide issued ON the call, Pre-print [E] is the Street mark "
                         "that stood when management guided, and Post-print [E] is where the "
                         "Street moved after. An Actual column belongs in Section 4, not here. "
                         "(v7.10.0 split the single Consensus column: one number cannot answer "
                         "both questions, and the four-column table answered only the second.)"
                         % (head, GUIDANCE_HEAD))
    ft = cfg.get("forwardTable")
    if ft:
        head = [str(h).strip() for h in (ft.get("head") or [])]
        if head != FORWARD_HEAD:
            fails.append("forwardTable head is %r; the contract is %r." % (head, FORWARD_HEAD))
    vt = cfg.get("valTable")
    if vt:
        head = [str(h).strip() for h in (vt.get("head") or [])]
        if len(head) < 4:
            fails.append("valTable head is %r. Section 10 is comparative: it needs a metric "
                         "column, the current figure, the company's OWN 3Y or 5Y average and a "
                         "peer column. A current-only table states a multiple without the two "
                         "references that make it mean anything." % (head,))
        else:
            rest = head[1:]
            if not any(re.search(r"(?i)\bavg\b|average", h) for h in rest):
                fails.append("valTable has no own-history average column (name it '3Y Avg' or "
                             "'5Y Avg'); head is %r." % (head,))
            if not any(re.search(r"(?i)peer", h) for h in rest):
                fails.append("valTable has no peer column (name it 'Peer Median' or 'Peer "
                             "Range'); head is %r. Where InSync returns no peer set for a real "
                             "multiple, keep the column, print 'n/a' - the data genuinely is "
                             "unavailable - and say so in valNote. Leave the cell EMPTY only "
                             "where the comparison itself is meaningless, as on the Price and "
                             "52-week range rows." % (head,))
    snap = cfg.get("snapshot") or {}
    _rows = snap.get("rows") or []
    def _base(lbl):
        return re.sub(r"\s*\([^)]*\)\s*$", "", str(lbl)).strip().lower()
    _tagged = {}
    for r in _rows:
        if not r:
            continue
        m = re.search(r"\(([^)]*)\)\s*$", str(r[0]))
        if m:
            _tagged.setdefault(m.group(1).strip().lower(), []).append(r)
    _p = _tagged.get("primary kpi", [])
    _k = _tagged.get("key margin", [])
    if _p and _k and _base(_p[0][0]) == _base(_k[0][0]):
        fails.append("the snapshot names %r as both the Primary KPI and the Key Margin. Rule 9 "
                     "wants seven distinct reads: where the sector's margin IS the driver KPI "
                     "(a bank's NIM, an insurer's combined ratio), keep it in the Primary KPI "
                     "row and put a second profitability metric in the Key Margin row."
                     % _base(_p[0][0]))
    for name, t in (("guidanceTable", gt), ("valTable", vt), ("snapshot", cfg.get("snapshot"))):
        if not (t and t.get("head")):
            continue
        n = len(t["head"])
        for r in (t.get("rows") or []):
            if name == "snapshot" and len(r) == n + 1:
                continue
            if len(r) != n:
                fails.append("%s row %r has %d cell(s) against a %d-column head."
                             % (name, (r[0] if r else r), len(r), n))
    return fails

_CUR_SYMBOL = {"$": ("USD", "CAD", "AUD", "HKD", "SGD", "NZD"), "€": ("EUR",), "£": ("GBP",),
               "¥": ("JPY", "CNY"), "₹": ("INR",), "CHF": ("CHF",), "R$": ("BRL",)}

def price_basis(cfg):
    pb = cfg.get("priceBasis")
    if not pb:
        return ["no priceBasis in the config. get_market_data returns a CURRENCY per row and "
                "does not guarantee one currency across a window (a single pull "
                "can mix sessions in two currencies). Record {currency, asOf, "
                "reactionSession, currencyConsistent} for the sessions you actually used."]
    fails = []
    cur = str(pb.get("currency") or "").strip().upper()
    if not cur:
        fails.append("priceBasis.currency is empty. Name the reporting currency of the price "
                     "series you used, taken from the payload's CURRENCY field.")
    if pb.get("currencyConsistent") is not True:
        fails.append("priceBasis.currencyConsistent is not true. Re-read the CURRENCY field on "
                     "EVERY session you used and drop any that disagrees; a mixed series cannot "
                     "produce a price or a reaction move.")
    if not pb.get("asOf"):
        fails.append("priceBasis.asOf is missing. Every current market figure sits on one "
                     "as-of date and the note must name it.")
    if cur:
        cells = []
        for r in (cfg.get("snapshot") or {}).get("rows") or []:
            if len(r) > 1 and re.search(r"(?i)price|reaction", str(r[0])):
                cells.append((str(r[0]), str(r[1])))
        for r in ((cfg.get("valTable") or {}).get("rows") or []):
            if len(r) > 1 and re.search(r"(?i)price|market cap|range", str(r[0])):
                cells.append((str(r[0]), str(r[1])))
        for label, cell in cells:
            for sym, allowed in _CUR_SYMBOL.items():
                if sym in cell and cur not in allowed:
                    fails.append("%r prints %r but priceBasis.currency is %s. Print the "
                                 "currency the price series is actually quoted in."
                                 % (label, cell, cur))
                    break
    return fails

CAPITAL_INTENSIVE_ROWS = {
    "REIT", "real estate services (not REIT)",
    "real estate owners and developers (not REIT)",
    "oil & gas producers / integrated / midstream", "oil & gas services and equipment",
    "oil and gas supply chain", "electric utilities", "gas and water utilities",
    "renewable energy", "electric and gas marketing and trading", "telecommunications",
    "cable and satellite", "metals and mining", "steel", "construction materials",
    "transportation equipment", "forestry, paper and wood products",
}
_CASHFLOW_ROW = re.compile(r"(?i)capex|capital expenditure|\bfcf\b|free cash flow|\bocf\b|"
                           r"operating cash flow|cash from operations|\bffo\b")
_PAYOUT_ROW = re.compile(r"(?i)dividend|payout|distribution|buyback|repurchase|shareholder return")
_GENERIC_ROW = re.compile(r"(?i)^\s*(total\s+)?(revenue|net revenue|sales)\s*$|adj eps|"
                          r"^\s*eps\b|earnings per share|^\s*(adj |adjusted )?"
                          r"(gross|ebit|ebitda|operating|net|pre-tax|pbt|fcf) margin")

def _row_names(cfg):
    res = [str(r.get("name") or "") for band in (cfg.get("results") or [])
           for r in (band.get("rows") or [])]
    kpi = [str(r.get("name") or "") for r in ((cfg.get("kpiTable") or {}).get("rows") or [])]
    snap = [str(_snap_cells(r)[0]) for r in ((cfg.get("snapshot") or {}).get("rows") or []) if r]
    return res, kpi, res + kpi + snap


def _canon(s):
    return re.sub(r"[^a-z0-9]+", " ", re.sub(r"\s*\([^)]*\)", "", str(s)).lower()).strip()


def relevance_contract(cfg):
    fails = []
    res, kpi, everywhere = _row_names(cfg)
    if not res:
        fails.append("the Results table has no rows. Section 4 is the graded print and the "
                     "note has nothing to grade; this is an empty pull, not a finished note.")
    if not kpi:
        fails.append("the KPI table has no rows. The standard is a MINIMUM of 2 "
                     "company-specific operating drivers, ranked - see Step 0. An empty "
                     "table means the KPI universe was never resolved, not that this issuer "
                     "has no KPIs.")
    prim = str(cfg.get("primaryKpi") or "").strip()
    if prim and res and kpi:
        pt = set(_canon(prim).split())
        def _specific_enough(a, b):
            if a == b:
                return True
            short = a if len(a) <= len(b) else b
            return len(short) >= 2
        def _hit(names):
            for x in names:
                xt = set(_canon(x).split())
                if (xt and pt and (pt <= xt or xt <= pt)
                        and _specific_enough(pt, xt)):
                    return True
            return False
        if not (_hit(res) and _hit(kpi)):
            where = "the Results table" if _hit(kpi) else "the KPI table"
            fails.append("the Primary KPI %r does not appear in %s. The sector's primary earnings "
                         "driver must be in BOTH: graded against consensus in Section 4 and "
                         "trended over five quarters in Section 6." % (prim, where))
    specific = [x for x in kpi if x and not _GENERIC_ROW.search(_canon(x))]
    if kpi and len(specific) < 2:
        fails.append("the KPI table carries %d company-specific row(s) (%s). The standard is a "
                     "MINIMUM of 2: a table of revenue, EPS and a margin is the generic spine, "
                     "not this company's operating drivers."
                     % (len(specific), ", ".join(specific) or "none"))
    row_id = str((cfg.get("sectorProfile") or {}).get("row") or "")
    if row_id in CAPITAL_INTENSIVE_ROWS:
        if not any(_CASHFLOW_ROW.search(x) for x in everywhere):
            fails.append("sector row %r is capital-intensive and the note carries no capex, FCF, "
                         "OCF or FFO line anywhere. An issuer whose tagged KPI "
                         "universe is subsidiary revenue lines only shows why: the spine metrics have to be "
                         "added from the sector rule, not waited for." % row_id)
        if not any(_PAYOUT_ROW.search(x) for x in everywhere):
            fails.append("sector row %r is an income sector and the note carries no dividend or "
                         "payout line anywhere. Revenue and EPS alone misdescribe it." % row_id)
    import datetime as _dt
    def _d(v):
        for f in ("%b %d, %Y", "%Y-%m-%d", "%d %b %Y"):
            try: return _dt.datetime.strptime(str(v).strip(), f).date()
            except ValueError: pass
        return None
    _fy = None
    _q = str(cfg.get("quarter") or "").strip()
    _m = re.match(r"(?:\d[QH])?(\d{2}|\d{4})$", _q)
    if _m:
        _y = int(_m.group(1))
        _fy = _y if _y > 1900 else 2000 + _y
    rep_d, prep_d = _d(cfg.get("reported")), _d(cfg.get("prepared"))
    if _fy and rep_d and abs(rep_d.year - _fy) > 1:
        fails.append("quarter %r is fiscal year %d but `reported` is %s. A fiscal period is "
                     "reported within a year either side of its own fiscal year, so these two "
                     "describe different periods. Either the label or the date is wrong - and "
                     "a note that grades the wrong quarter is complete, gate-clean and useless."
                     % (_q, _fy, cfg.get("reported")))
    if rep_d and prep_d and rep_d > prep_d:
        fails.append("reported %s is AFTER prepared %s. A wrap grades a quarter that has already "
                     "been reported; a future report date means this is a preview and belongs in "
                     "earnings-preview." % (cfg.get("reported"), cfg.get("prepared")))
    return fails

def one_currency_per_table(cfg):
    fails = []
    for name in ("valTable", "guidanceTable", "forwardTable", "snapshot"):
        t = cfg.get(name) or {}
        syms = set()
        for r in (t.get("rows") or []):
            _label = str((r.get("name") if isinstance(r, dict) else (list(r)[0] if r else "")) or "")
            _exempt = bool(_CUR_EXEMPT_ROW.search(_label))
            _own = set()
            for cell in ([r.get("u")] if isinstance(r, dict) else list(r)[1:]):
                c = _cell_text(cell)
                for sym, key in (("$", "USD"), ("€", "EUR"), ("£", "GBP"), ("¥", "JPY"),
                                 ("₹", "INR"), ("R$", "BRL")):
                    if sym in c:
                        _own.add(key)
                for code in ("USD", "EUR", "GBP", "JPY", "CHF", "CAD", "AUD", "INR", "BRL",
                             "CNY", "HKD", "SGD"):
                    if re.search(r"\b%s\b|%s[0-9]" % (code, code), c):
                        _own.add(code)
            if _exempt:
                if len(_own) > 1:
                    fails.append("%s row %r states one metric in %s. A price, market cap or "
                                 "reaction row may carry the listing currency rather than the "
                                 "reporting one, but not two currencies across its own cells."
                                 % (name, _label, " and ".join(sorted(_own))))
            else:
                syms |= _own
        if len(syms) > 1:
            fails.append("%s mixes %s in one table. Fundamentals stay in the reporting currency "
                         "and price in the listing currency, but never inside the same table: "
                         "name the basis in the caption and split the rows."
                         % (name, " and ".join(sorted(syms))))
    fails += _one_currency_per_note(cfg)
    return fails

_CUR_EXEMPT_ROW = re.compile(r"(?i)price|market cap|reaction|per share amount|dividend per")

def _note_currencies(cfg):
    seen = {}
    def note(cur, where):
        seen.setdefault(cur, [])
        if where not in seen[cur]: seen[cur].append(where)
    def scan(cell, where):
        c = str(cell)
        for sym, key in (("$", "USD"), ("\u20ac", "EUR"), ("\u00a3", "GBP"), ("\u00a5", "JPY"),
                         ("\u20b9", "INR")):
            if sym in c: note(key, where)
        for code in ("USD", "EUR", "GBP", "JPY", "CHF", "CAD", "AUD", "INR", "BRL",
                     "CNY", "HKD", "SGD", "GBp"):
            if re.search(r"%s(?:[BTM]\b|\b|[0-9])" % code, c):
                note("GBP" if code == "GBp" else code, where)
    for sec in (cfg.get("results") or ()):
        for r in (sec.get("rows") or ()):
            if not _CUR_EXEMPT_ROW.search(str(r.get("name") or "")):
                scan(r.get("u"), "the Results table")
    for r in ((cfg.get("kpiTable") or {}).get("rows") or ()):
        if not _CUR_EXEMPT_ROW.search(str(r.get("name") or "")):
            scan(r.get("u"), "the KPI table")
    for name, where in (("guidanceTable", "the guidance table"),
                        ("forwardTable", "the forward table")):
        for r in ((cfg.get(name) or {}).get("rows") or ()):
            if _CUR_EXEMPT_ROW.search(str(list(r)[0] if r else "")): continue
            for cell in list(r)[1:]: scan(cell, where)
    return seen

def _one_currency_per_note(cfg):
    seen = _note_currencies(cfg)
    if len(seen) < 2:
        return []
    detail = "; ".join("%s in %s" % (k, " and ".join(v)) for k, v in sorted(seen.items()))
    return ["the note prints fundamentals in more than one currency - %s. An issuer reports in "
            "ONE currency and every fundamental table must use it; only price, market cap and the "
            "reaction row may carry the listing currency. Convert the odd table or say in the "
            "caption which basis each uses." % detail]

_GUIDANCE_SOURCE_VALUES = ("get_guidance", "release", "transcript", "none", "unavailable")


def _wrap_parse_date(raw):
    import datetime as _dt
    if not raw:
        return None
    s = str(raw).strip()
    for cand in (s, s.split("T")[0].split(" ")[0], s[:10]):
        for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%d-%b-%Y", "%Y/%m/%d", "%d/%m/%Y"):
            try:
                return _dt.datetime.strptime(cand, fmt).date()
            except ValueError:
                continue
    return None


def guidance_freshness_gate(cfg):
    fails = []
    gf = cfg.get("guidanceFreshness")
    has_guide_table = bool(cfg.get("guidanceTable"))
    has_forward = bool(cfg.get("forwardTable"))

    if not isinstance(gf, dict):
        fails.append(
            "no `guidanceFreshness` block. Section 7 and every guidance-graded verdict rest on "
            "`get_guidance` being CURRENT, and nothing here checks that. Record "
            "{callDate, newestGuideDate, guidanceSource, rowsReturned} from the payload you "
            "already pulled. Guidance rows can be stamped a day after the print - "
            "a same-day run reads last quarter's guides with no tell on the page.")
        return fails

    call_d = _wrap_parse_date(gf.get("callDate"))
    guide_d = _wrap_parse_date(gf.get("newestGuideDate"))
    src = str(gf.get("guidanceSource") or "").strip().lower()
    rows = gf.get("rowsReturned")

    if src and src not in _GUIDANCE_SOURCE_VALUES:
        fails.append("guidanceFreshness.guidanceSource=%r is not one of %s."
                     % (src, "/".join(_GUIDANCE_SOURCE_VALUES)))

    if has_guide_table and not call_d:
        fails.append("guidanceFreshness.callDate is missing or unparseable, so nothing can tell "
                     "this quarter's guides from last quarter's. The guidance table is graded "
                     "against them.")

    if has_guide_table and call_d and guide_d and guide_d < call_d:
        fails.append(
            "[guidance predates print] the newest GUIDEDATE is %s, BEFORE the %s call. These are "
            "the PREVIOUS period's guides, returned because `get_guidance` hands back the newest "
            "rows that exist rather than the newest that were given. Quoting them prints a "
            "superseded guide as current, and in a Wrap that also corrupts every verdict graded "
            "against guidance. Take the guides from the earnings release (any size) or the "
            "transcript and set guidanceSource accordingly; keep these rows as Prior [G]."
            % (guide_d.isoformat(), call_d.isoformat()))

    if has_guide_table and call_d and not guide_d:
        fails.append(
            "guidanceFreshness.newestGuideDate is missing, so the staleness check could not run. "
            "GUIDEDATE ships on every `get_guidance` row — record the newest one. A superseded "
            "guide reads exactly like a current one.")

    if has_forward:
        try:
            n = int(rows) if rows is not None else None
        except (TypeError, ValueError):
            n = None
        if n is None:
            fails.append(
                "forwardTable is present — the substitute for an issuer that gives NO guidance — "
                "but guidanceFreshness.rowsReturned is not recorded. Record how many rows "
                "`get_guidance` actually returned.")
        elif n > 0:
            fails.append(
                "forwardTable is present but `get_guidance` returned %d row(s). forwardTable is "
                "only for an issuer that gives no guidance at all; where a guide exists the note "
                "must carry guidanceTable." % n)
        elif src not in ("none", "unavailable"):
            fails.append(
                "[zero rows is not proof] forwardTable is present on a zero-row fetch, but "
                "guidanceSource is %r. Zero rows has THREE causes that are identical in the "
                "payload — a genuinely non-guiding issuer, a failed fetch, and guidance that has "
                "not ingested yet (guides can land a day after the print). Parse the "
                "earnings release: if it carries no guide, set guidanceSource 'none'; if it could "
                "not be read, 'unavailable', and say so on the page. Do not assert a fact about "
                "the company from a fact about the fetch."
                % (src or "unset"))
    return fails


def guidance_consensus_gate(cfg):
    fails = []
    gt = cfg.get("guidanceTable")
    if not gt:
        return fails
    probe = cfg.get("guidanceConsensusProbe")
    head = [str(h).strip() for h in (gt.get("head") or [])]
    if head == GUIDANCE_HEAD_LEGACY:
        fails.append(
            "guidanceTable still carries the v7.9.0 four-column head. It shows only the "
            "POST-print consensus, so the table cannot answer whether the guide beat what the "
            "Street expected — the question its own shape implies. Widen to %r and fill "
            "`guidanceConsensusProbe`." % (GUIDANCE_HEAD,))
        return fails
    if not isinstance(probe, dict):
        fails.append(
            "guidanceTable carries a Pre-print [E] column but there is no "
            "`guidanceConsensusProbe` recording where those figures came from. The pre-print "
            "forward mark is a SEPARATE pull from the Results probe (`consensusProbe` covers "
            "the closed quarter only) — run get_estimates on the guided ltcodes with "
            "latest={enabled:false} and an updatedate window ending at the print, take the last "
            "snapshot, and record it.")
        return fails
    if not _wrap_parse_date(probe.get("asOf")):
        fails.append("guidanceConsensusProbe.asOf is missing or unparseable. The pre-print "
                     "comparator is only meaningful with the date it was taken.")
    if not _wrap_parse_date(probe.get("postAsOf")):
        fails.append("guidanceConsensusProbe.postAsOf is missing. The post-print column needs "
                     "its own stamp — the two columns are different dates by definition.")
    pr = probe.get("probed")
    if not isinstance(pr, list) or not pr:
        fails.append("guidanceConsensusProbe.probed is empty. Name every guided metric you "
                     "probed, with its ltcode, the value found and the broker count — or "
                     "`found: null` plus the evidence that no pre-print mark exists.")
    else:
        for i, row in enumerate(pr):
            if not isinstance(row, dict):
                fails.append("guidanceConsensusProbe.probed[%d] is not an object." % i)
                continue
            if not str(row.get("name") or "").strip():
                fails.append("guidanceConsensusProbe.probed[%d] has no `name`." % i)
            if row.get("found") is None and not str(row.get("evidence") or "").strip():
                fails.append(
                    "guidanceConsensusProbe.probed[%d] (%r) reports no pre-print mark but "
                    "carries no `evidence`. A missing comparator has to be PROVED missing, the "
                    "same bar `consensusProbe` already applies to Results rows."
                    % (i, row.get("name")))
    return fails


GUIDANCE_BASIS = {
    "quarter-bar": "Prior is the guide the company had given FOR the quarter just reported. "
                   "New is the guide issued on the call.",
    "forward-vintage": "Prior is the previous vintage of the same forward guide. New is the "
                       "vintage issued on the call.",
    "annual-roll": "This issuer guides on a full-year basis only. Prior is the outgoing fiscal "
                   "year's guide and New is the guide for the incoming year, so the two target "
                   "DIFFERENT periods and the change is a roll-forward, not a revision.",
}

_GROWTH_CUE = re.compile(r"(?i)\bgrowth\b|\borganic\b|\bcomps?\b|like[- ]for[- ]like|\bY/Y\b")

def growth_kind_contract(cfg):
    fails = []
    ktab = {}
    for r in ((cfg.get("kpiTable") or {}).get("rows") or []):
        if isinstance(r, dict) and r.get("name"):
            ktab[_canon_metric(r.get("name"))] = str(r.get("kind") or "")
    for band in (cfg.get("results") or []):
        for r in (band.get("rows") or []):
            if not isinstance(r, dict):
                continue
            name = str(r.get("name") or "")
            kind = str(r.get("kind") or "")
            if kind:
                if kind not in ("level", "rate", "growth"):
                    fails.append("results row %r declares kind %r. The vocabulary is level, rate "
                                 "or growth, the same three `kpiTable` uses." % (name, kind))
                continue
            if ktab.get(_canon_metric(name)) == "growth":
                fails.append(
                    "results row %r has no `kind`, but `kpiTable` declares the SAME metric as "
                    "`growth`. One note cannot say two things about what a number is. An "
                    "already-Y/Y series in Section 4 prints the change IN the rate under a column "
                    "headed Y/Y, so declare `kind: \"growth\"` on the results row too and the "
                    "renderer marks it and explains it in the caption." % name)
            elif str(r.get("u") or "") == "%" and _GROWTH_CUE.search(name):
                fails.append(
                    "results row %r reads like an already-Y/Y series and declares no `kind`. If it "
                    "is one, set `kind: \"growth\"` - the Y/Y and sequential columns then print "
                    "the change IN that rate, marked and explained. If the row is a LEVEL rate "
                    "such as a margin, set `kind: \"rate\"` to say so once." % name)
    return fails


_VALNOTE_PROVENANCE_RE = re.compile(
    r"(?i)\b(get_valuation|summarize\s+true|summarize:\s*true|data[_ ]points|"
    r"daily\s+points|sample\s+(?:holds|of|size)|start_date)\b")


def valnote_provenance_gate(cfg):
    fails = []
    for key in ("valNote", "valProse"):
        val = cfg.get(key)
        if not val:
            continue
        items = val if isinstance(val, (list, tuple)) else [val]
        for item in items:
            text = str(item or "")
            m = _VALNOTE_PROVENANCE_RE.search(text)
            if m:
                fails.append(
                    "SECTION 10 PROVENANCE ON THE PAGE: %s says %r. The phrase %r names how the "
                    "figure was fetched, not what it means. `valCap` was retired in v7.11.0 and "
                    "its methodology went with it - moving that sentence into %s puts the same "
                    "text back on the same page. Record the call, its arguments and `data_points` "
                    "in `valProvenance`; keep %s to what the sector is valued on, the currency "
                    "basis, and any `n/a` with the reason the figure was unavailable."
                    % (key, text[:160], m.group(0), key, key))
    return fails


def note_contract(cfg):
    fails = list(growth_kind_contract(cfg))
    fails.extend(valnote_provenance_gate(cfg))
    uni = cfg.get("kpiUniverse") or []
    sec = str(cfg.get("secondaryKpi") or "").strip()
    if not sec:
        fails.append("no secondaryKpi in the config. Row 2 of the Event Snapshot is the second "
                     "Key KPI and was the last unpinned choice in a fixed grid. Run "
                     "`python references/kpi_gate.py --pick-secondary` and record what it returns.")
    elif uni:
        low = [str(x).strip().lower() for x in uni]
        if sec.lower() not in low and not any(sec.lower() in x or x in sec.lower() for x in low):
            fails.append("secondaryKpi %r is not a member of kpiUniverse. It must come from the "
                         "resolved universe, like the Primary KPI." % sec)
        if str(cfg.get("primaryKpi") or "").strip().lower() == sec.lower():
            fails.append("secondaryKpi is the same as primaryKpi. Rows 1 and 2 of the snapshot "
                         "are two different reads.")
    if cfg.get("guidanceTable") and cfg.get("forwardTable"):
        fails.append("both guidanceTable and forwardTable are present. forwardTable is the "
                     "substitute for an issuer that gives NO guidance; where a guide exists the "
                     "guidance table carries it.")
    if not cfg.get("guidanceTable") and not cfg.get("forwardTable"):
        fails.append("Section 7 has no table. Where the company guides, fill `guidanceTable`. "
                     "Where `get_guidance` returned nothing (an issuer that "
                     "publishes no forward guide), fill `forwardTable` with the forward consensus "
                     "you pulled: head `[\"Forward Metric\",\"Consensus [E]\",\"Basis\"]`. A "
                     "prose-only forward section is what wrap_eval exists to refuse.")
    if cfg.get("guidanceTable"):
        gb = str(cfg.get("guidanceBasis") or "").strip()
        if gb not in GUIDANCE_BASIS:
            fails.append("guidanceBasis must be one of %s. `Prior [G]` is ambiguous without it: "
                         "it can be the bar set for the quarter just reported, or the previous "
                         "vintage of a standing forward guide, and the reader cannot tell which "
                         "is on the page." % sorted(GUIDANCE_BASIS))
    if not str(cfg.get("revisionsScope") or "").strip():
        fails.append("no revisionsScope in the config. Say in one line which lines were checked "
                     "for post-print revisions and what came back, whether or not a revisions "
                     "grid is shown. An omitted row and an unrevised row look identical.")
    _REQUIRED = (("summary",   "the Executive Summary"),
                 ("variant",   "Section 2, Variant View"),
                 ("drove",     "Section 5, What Drove the Quarter"),
                 ("kpiHier",   "Section 6, KPI Hierarchy"),
                 ("mgmt",      "Section 8, Management Commentary"),
                 ("monitor",   "Section 9, Key Monitoring Variables"),
                 ("sources",   "the Sources line"))
    for _k, _where in _REQUIRED:
        _v = cfg.get(_k)
        if not _v or (isinstance(_v, (list, tuple, dict)) and not len(_v)):
            fails.append("%r is empty, so %s renders as a bare heading with nothing under it. "
                         "The section is part of the FIXED structure and cannot be dropped: "
                         "fill it, or say on the page why the data is unavailable." % (_k, _where))
    for _k, _where in (("snapshot", "Section 3, the Event Snapshot"),
                       ("valTable", "Section 10, Valuation Context")):
        if not ((cfg.get(_k) or {}).get("rows") or []):
            fails.append("%s.rows is empty, so %s prints a header row with no data under it. "
                         "Where a figure is genuinely unavailable print 'n/a' in the row and say "
                         "so in the caption - never an empty table." % (_k, _where))
    for _k, _where in (("takeaway", "the Section 4 Takeaway"),
                       ("valNote",  "the Section 10 valuation note")):
        if not str(cfg.get(_k) or "").strip():
            fails.append("%r is empty, so %s is blank. It carries the basis and the caveat; a "
                         "blank one implies a completeness the data may not have." % (_k, _where))
    return fails

_VAL_NUM = re.compile(r"^\s*([0-9][0-9,]*\.?[0-9]*)\s*(x|%)\s*$")
VAL_SANITY_BAND = 20.0

def _cell_value(c):
    c = _peer_cell(c)
    if isinstance(c, dict) and c.get("v") is not None:
        u = str(c.get("u") or "")
        kind = "%" if u.startswith("%") else ("x" if u == "x" else "")
        return (float(c["v"]), kind)
    m = _VAL_NUM.match(_cell_text(c))
    return (float(m.group(1).replace(",", "")), m.group(2)) if m else (None, None)

_FX_DERIVED_MULT = re.compile(
    r"(?i)\bP\s*/\s*(?:E|S|B|FCF|OCF)\b|\bPEG\b|\bEV\s*/|earnings\s+yield|fcf\s+yield|"
    r"dividend\s+yield|market\s+cap")

def valuation_fx_basis(cfg):
    pb = cfg.get("priceBasis") or {}
    listing = str(pb.get("currency") or "").strip().upper()
    if not listing:
        return []
    reporting = [c for c in _note_currencies(cfg) if c.upper() != listing]
    if not reporting:
        return []
    if str(cfg.get("valFxBasis") or "").strip():
        return []
    vt = cfg.get("valTable") or {}
    head = [str(h).strip() for h in (vt.get("head") or [])]
    try:
        ci = next(i for i, h in enumerate(head) if re.search(r"(?i)current", h))
    except StopIteration:
        return []
    hit = []
    for r in (vt.get("rows") or []):
        label = str(list(r)[0] if r else "")
        if not _FX_DERIVED_MULT.search(label):
            continue
        if len(r) <= ci:
            continue
        cur, ckind = _cell_value(r[ci])
        if cur is not None and ckind is not None:
            hit.append(label)
    if not hit:
        return []
    return ["this issuer reports in %s and is priced in %s, and %s put a %s price over a %s "
            "fundamental: %s. A vendor multiple on a dual-currency issuer is cross-currency unless "
            "the vendor converted, and nothing in the payload says which. A dual-currency "
            "issuer can show an NTM P/E computed as a GBP price over a USD EPS, understating the "
            "multiple, and the 3Y history can carry the same error so the own-average check "
            "passes. Reconcile it: divide the price you printed by the forward EPS you pulled. If "
            "that reproduces the vendor's multiple, the vendor did NOT convert - print `n/a` for "
            "those rows and say so in `valNote`. Either way record what you found in `valFxBasis`. "
            "This gate cannot do the conversion for you: there is no FX rate in the payload and an "
            "invented one is a fabricated figure."
            % (", ".join(sorted(reporting)), listing, "they" if len(hit) > 1 else "it",
               listing, ", ".join(sorted(reporting)), ", ".join(repr(h) for h in hit))]


def valuation_sanity(cfg):
    vt = cfg.get("valTable") or {}
    head = [str(h).strip() for h in (vt.get("head") or [])]
    if len(head) < 3:
        return []
    try:
        ci = next(i for i, h in enumerate(head) if re.search(r"(?i)current", h))
    except StopIteration:
        return []
    ai = next((i for i, h in enumerate(head[1:], 1)
               if re.search(r"(?i)\bavg\b|average", h)), None)
    pi = next((i for i, h in enumerate(head[1:], 1)
               if re.search(r"(?i)peer", h)), None)
    if ai is None and pi is None:
        return []
    fails = []
    for r in (vt.get("rows") or []):
        cur, ckind = _cell_value(r[ci]) if len(r) > ci else (None, None)
        if cur is None or ckind is None:
            continue
        ref, which, rtext = None, "", ""
        for idx, nm in ((ai, "own average"), (pi, "peer median")):
            if idx is None or len(r) <= idx:
                continue
            rv, rk = _cell_value(r[idx])
            if rv is not None and rk == ckind:
                ref, which, rtext = rv, nm, _cell_text(r[idx])
                break
        if ref is None:
            continue
        avg = ref
        if not cur or not avg:
            continue
        if ai is not None and pi is not None and len(r) > max(ai, pi):
            _a, _ka = _cell_value(r[ai]); _p, _kp = _cell_value(r[pi])
            if _a is not None and _p is not None and _ka == _kp:
                if _a and _p:
                    _rr = _a / _p if _a > _p else _p / _a
                    if _rr > VAL_SANITY_BAND:
                        fails.append(
                            "valTable row %r prints an own average of %s against a peer median "
                            "of %s, a %.0fx gap between two references for the SAME metric. One "
                            "of them is wrong: check the get_valuation payload for a unit or "
                            "currency error before printing either."
                            % (_cell_text(r[0]), _cell_text(r[ai]).strip(),
                               _cell_text(r[pi]).strip(), _rr))
        ratio = cur / avg if cur > avg else avg / cur
        if ratio > VAL_SANITY_BAND:
            fails.append("valTable row %r prints %s against a %s of %s, a %.0fx gap. "
                         "A multiple does not move that far against its own history: check the "
                         "get_valuation payload for a unit or currency error, and drop the row "
                         "with a line in valNote if the feed cannot be reconciled."
                         % (_cell_text(r[0]), _cell_text(r[ci]), which, rtext.strip(), ratio))
    return fails

def staleness_contract(cfg):
    ip = cfg.get("identityProbe")
    if not isinstance(ip, dict) or not ip:
        return []
    fails = []
    _lfq = str(ip.get("latestFiscalQuarter") or ip.get("latest_fiscal_quarter") or "").strip()
    if not _lfq:
        fails.append("identityProbe records no latestFiscalQuarter. `search_tickerid` returns "
                     "it on every call and it is the only thing in the config that can tell a "
                     "current wrap from one written a quarter behind the database.")
    else:
        _wq, _lq = _period_key(cfg.get("quarter")), _period_key(_lfq)
        if _wq and _lq and _wq < _lq:
            fails.append(
                "this note wraps %r but identityProbe.latestFiscalQuarter is %r, so a LATER "
                "quarter has already reported. Wrapping the older one produces a complete, "
                "numerically correct note for the wrong period - the masthead says %r while "
                "the KPI grid still ends at %r. If you meant the latest print, re-resolve and "
                "rebuild; if you deliberately want the older quarter, say so to the user first, "
                "because nothing downstream of here will mention it."
                % (str(cfg.get("quarter")), _lfq, str(cfg.get("quarter")), _lfq))
        elif _wq is None or _lq is None:
            fails.append(
                "cannot compare the wrapped quarter %r with identityProbe.latestFiscalQuarter "
                "%r - one of them is not a label this skill reads (2Q26, 1H26, 2026). Fix the "
                "label rather than leaving the staleness check unable to run."
                % (str(cfg.get("quarter")), _lfq))
    return fails


def graded_column_gate(cfg):
    rows = [r for sec in (cfg.get("results") or ()) for r in (sec.get("rows") or ())]
    if not rows:
        return [], []
    shown = cfg.get("consensusColumn", True) is not False
    graded = [r for r in rows if r.get("c") is not None]
    ungraded = [str(r.get("name")) for r in rows if r.get("c") is None]
    fails, warns = [], []
    if shown and not graded:
        fails.append(
            "the Results table shows a Consensus column and NOT ONE of its %d rows carries a "
            "consensus value, so Section 4 would print three columns of dashes and grade "
            "nothing. This is the tier-3 case in SKILL.md: set `consensusColumn: false` and "
            "write `consensusBasis` naming what you graded against instead (the guidance "
            "midpoint, or a cited preview). If consensus DOES exist, the probe missed it - "
            "margins and segment lines carry it more often than the headline pull suggests."
            % len(rows))
    if not shown and graded:
        fails.append(
            "`consensusColumn` is false but %d Results row(s) carry a consensus value (%s). "
            "Dropping the column would hide a number the note actually has. Either show the "
            "column or clear those values."
            % (len(graded), ", ".join(str(r.get("name")) for r in graded[:3])))
    if not shown and not str(cfg.get("consensusBasis") or "").strip():
        fails.append(
            "`consensusColumn` is false and `consensusBasis` is empty. A note that grades "
            "against something other than consensus must say so in one line, on the page - "
            "SKILL.md: 'drop the consensus column, grade against the guidance midpoint only, "
            "and say so in one line.'")
    if shown and ungraded:
        warns.append(
            "%d of %d Results rows print a dash in the Consensus column: %s. A dash is only "
            "honest if the probe PROVED that line unconsensused - re-read `consensusProbe` "
            "and confirm each one carries its `evidence`. Nothing else in this run can tell a "
            "proven gap from a line nobody asked about."
            % (len(ungraded), len(rows), ", ".join(ungraded[:6])
               + (", ..." if len(ungraded) > 6 else "")))
    return fails, warns

_FIG_TABLES = (("valTable", "Section 10, Valuation Context"),
               ("guidanceTable", "Section 7, Guidance and Revisions"),
               ("forwardTable", "Section 7, Guidance and Revisions"))

def _lead_number(s):
    m = re.search(r"-?\d[\d,]*(?:\.\d+)?", str(s).replace("\u2009", ""))
    return float(m.group(0).replace(",", "")) if m else None

def table_figures_gate(cfg):
    fails = []
    for key, where in _FIG_TABLES:
        tb = cfg.get(key)
        if not isinstance(tb, dict):
            continue
        for r in (tb.get("rows") or []):
            if not isinstance(r, (list, tuple)) or not r:
                continue
            label = _cell_text(r[0])
            for j, c in enumerate(r[1:], 1):
                if isinstance(c, dict):
                    continue
                figs = _FIGURE_RE.findall(str(c))
                if figs:
                    fails.append(
                        "%s: row %r column %d is the hand-typed string %r. A cell carrying a "
                        "figure must be a VALUE plus a unit token so the renderer formats it "
                        '- {"v": 18.2, "u": "x"} for a figure, {"lo": 6.55, "hi": 6.80, '
                        '"u": "$"} for a range. Until v7.7.0 these two tables were the only '
                        "numbers in the note that no layer owned, and a fabricated price, "
                        "multiple or guidance consensus rendered at exit 0 with every layer "
                        "passing. Plain text is still fine where it carries no figure "
                        "('n/a', 'InSync NTM', 'Jul 23, 2026 close')."
                        % (where, label, j, str(c)))
    snap = {}
    for r in ((cfg.get("snapshot") or {}).get("rows") or ()):
        cells = _snap_cells(r)
        if cells and cells[0]:
            snap.setdefault(_canon_metric(cells[0]), str(cells[1]).strip())
    vt = cfg.get("valTable") or {}
    head = [str(h).strip() for h in (vt.get("head") or [])]
    ci = next((i for i, h in enumerate(head) if re.search(r"(?i)current", h)), 1)
    for r in (vt.get("rows") or []):
        if not isinstance(r, (list, tuple)) or len(r) <= ci:
            continue
        key = _canon_metric(_cell_text(r[0]))
        for cand in (key, "current " + key, key.replace("current ", "")):
            if cand in snap:
                got, want = _cell_text(r[ci]).strip(), snap[cand].strip()
                gv, wv = _lead_number(got), _lead_number(want)
                if (gv is not None and wv is not None
                        and house_round(gv, 2) != house_round(wv, 2)):
                    fails.append(
                        "valTable row %r prints %r in the Current column while the Event "
                        "Snapshot prints %r for the same metric. One note, one figure: these "
                        "cannot both be right, and before v7.7.0 nothing compared them."
                        % (_cell_text(r[0]), got, want))
                break
    return fails

def validation_check(html, tk):
    try:
        sys.path.insert(0, HERE)
        import validate_wrap
        res = validate_wrap.validate(html, CSS)
    except SystemExit:
        raise
    except Exception as e:
        raise SystemExit("VALIDATION GATE FAIL " + tk + ": the validation layers could not be "
                         "run (%s: %s). They cannot be skipped; fix the install."
                         % (type(e).__name__, e))
    bad = [(n, i) for n, i in res.items() if i]
    if bad:
        lines = ["%s: %s" % (n, m) for n, issues in bad for m in issues]
        raise SystemExit("VALIDATION FAIL %s (%d of %d layers):\n  - %s\n\nThe HTML is on disk; "
                         "re-run `python references/validate_wrap.py <file>.html` as you fix the "
                         "note. Never loosen a layer to make a run pass."
                         % (tk, len(bad), len(validate_wrap.LAYERS), "\n  - ".join(lines)))
    return res

def basis_mismatch_advisory(cfg, ticker):
    out = []
    for band in (cfg.get("results") or []):
        for r in (band.get("rows") or []):
            if not isinstance(r, dict) or str(r.get("u") or "") != "%":
                continue
            try:
                a, c = float(r.get("a")), float(r.get("c"))
            except (TypeError, ValueError):
                continue
            if abs(a - c) < 10.0:
                continue
            out.append(
                "WARN basis_mismatch %s: row %r prints an actual of %s%% against a consensus of "
                "%s%%, a %spp gap. On a rate line that size of gap is far more often an "
                "ADJUSTED-basis estimate graded against a REPORTED actual than a real miss - "
                "VERIFIED on a spirits maker, where IS_P_109-M actual 0.44%% sat against its own "
                "21.40%% consensus while the adjusted twin IS_P_110-M was 23.10%% against 21.90%%. "
                "Check whether the adjusted twin carries both sides of the pair, and grade the "
                "basis the estimate was built on. Nothing else in this run can tell a basis "
                "mismatch from a genuine miss."
                % (ticker, r.get("name"), house_fmt(a, 2), house_fmt(c, 2),
                   house_fmt(abs(a - c), 1)))
    return out


_METRIC_KIND = (("revenue", re.compile(r"revenue|sales|turnover", re.I)),
                ("profit", re.compile(r"\bebit|ebitda|operating (?:income|profit)|profit|\bincome\b", re.I)),
                ("volume", re.compile(r"volume|units|shipments|deliveries", re.I)),
                ("price", re.compile(r"price|pricing|\bASP\b|yield", re.I)))

def _metric_kind(name):
    for kind, pat in _METRIC_KIND:
        if pat.search(str(name or "")):
            return kind
    return None

def kpi_nesting(cfg):
    acc = cfg.get("kpiNesting") or {}
    if isinstance(acc, dict) and acc.get("accepted") and str(acc.get("reason", "")).strip():
        return []
    prim = str(cfg.get("primaryKpi") or "").strip()
    if not prim:
        return []
    try:
        sys.path.insert(0, HERE)
        import kpi_gate as _kg
    except Exception:
        return []
    uni = [str(u) for u in (cfg.get("kpiUniverse") or []) if str(u).strip()]
    def _is_kpi(name):
        n = _kg._norm(name)
        return any(n and (n in _kg._norm(u) or _kg._norm(u) in n) for u in uni)
    rows = [r for band in (cfg.get("results") or []) for r in (band.get("rows") or [])
            if isinstance(r, dict) and _is_kpi(r.get("name", ""))]
    pn = _kg._norm(prim)
    me = [r for r in rows if pn and (pn in _kg._norm(r.get("name", ""))
                                     or _kg._norm(r.get("name", "")) in pn)]
    if not me:
        return []
    me = me[0]
    try:
        a = abs(float(me.get("a")))
    except (TypeError, ValueError):
        return []
    if str(me.get("u") or "").startswith("%"):
        return []
    fails = []
    for r in rows:
        if r is me or r.get("u") != me.get("u"):
            continue
        if _metric_kind(me.get("name")) != _metric_kind(r.get("name")):
            continue
        try:
            b = abs(float(r.get("a")))
        except (TypeError, ValueError):
            continue
        if not b or a >= 0.5 * b:
            continue
        shared = set(_kg.toks(me.get("name", ""))) & set(_kg.toks(r.get("name", "")))
        if len(shared) < 2:
            continue
        fails.append(
            "Primary KPI %r prints %s against %r at %s in the same units - %.0f%% of it - and the "
            "two share the tokens %s. That is what a COMPONENT of another line looks like, and it "
            "puts the smaller line above its own parent in the Event Snapshot and Section 6, which "
            "is titled KPI Hierarchy. Do NOT re-pick from the model side: the tie-break is what "
            "makes the choice reproducible, and the durable fix is the InSync tagging. If the "
            "hierarchy is right as it stands, say so once with "
            "kpiNesting: {\"accepted\": true, \"reason\": \"...\"}."
            % (me.get("name"), me.get("a"), r.get("name"), r.get("a"), 100.0 * a / b,
               ", ".join(sorted(shared))))
    return fails


def kpi_gate_check(cfg):
    tk = str(cfg.get("ticker", ""))
    if not str(cfg.get("primaryKpi") or "").strip():
        raise SystemExit("KPI GATE FAIL " + tk + ": no primaryKpi in the config. Decision 1 of "
                         "Step 0 IS the Primary KPI: resolve the universe from the MCP, then "
                         "pick it mechanically with `python references/kpi_gate.py "
                         "--pick-primary`. An absent Primary KPI is not an empty check, it is "
                         "a Step 0 that was never run.")
    prov = str(cfg.get("kpiProvenance") or "").strip()
    if not prov:
        raise SystemExit("KPI GATE FAIL " + tk + ": no kpiProvenance in the config. Record which "
                         "tier of the Step 0 ladder actually produced kpiUniverse - "
                         "mcp_top_kpi, mcp_kpi_tier or mcp_discovery - and disclose any tier "
                         "past the first in kpiCap and sources.")
    if not cfg.get("kpiUniverse"):
        raise SystemExit("KPI GATE FAIL " + tk + ": no kpiUniverse in config. Run the Step 0 MCP "
                         "KPI Resolution Ladder (top_kpi -> all KPI tiers -> keyword line-item "
                         "discovery proven against get_actuals) and record kpiUniverse + "
                         "kpiProvenance.")
    if prov == "mcp_discovery" and not cfg.get("kpiDiscovery"):
        raise SystemExit("KPI GATE FAIL " + tk + ": kpiProvenance is mcp_discovery but "
                         "kpiDiscovery (the keyword + proved-ltcode audit trail) is missing.")
    if prov == "mcp_discovery" and not str(cfg.get("primaryKpiBasis") or "").strip():
        raise SystemExit(
            "KPI GATE FAIL " + tk + ": kpiProvenance is mcp_discovery, so the KPI universe "
            "carries no tags and `--pick-primary` cannot rank it. The Primary KPI at this tier "
            "is YOUR judgement, so record it: `primaryKpiBasis` must say why this line is the "
            "driver - normally that the issuer's own results statement leads on it. For one "
            "issuer, three legitimate orderings of one proved universe gave three different "
            "Primary KPIs, all at exit 0, because the tie-break was ranking the order you typed.")
    try:
        sys.path.insert(0, HERE)
        import kpi_gate
    except Exception as e:
        raise SystemExit("KPI GATE FAIL " + tk + ": kpi_gate could not be loaded (%s). The gate "
                         "cannot be skipped; fix the install." % e)
    tier, primary, matched, prov2, why = kpi_gate.classify_mcp(
        cfg["primaryKpi"], cfg["kpiUniverse"], prov)
    kpi_gate.report_mcp(tier, primary, matched, prov2, why)
    if tier == "FAIL":
        raise SystemExit("KPI GATE FAIL " + tk + ": Primary KPI %r is not a valid KPI for this "
                         "company (%s)." % (cfg["primaryKpi"], why))


def render(cfg, out_pdf):
    _tk = str(cfg.get("ticker",""))
    _enforce("UNIT GATE", _tk, unit_contract(cfg))
    cfg, _thin_rows = ungrade_thin_panels(cfg)
    for _n in _thin_rows:
        sys.stderr.write("WARN thin_panel %s: row %r has a zero-broker consensus, so it prints "
                         "ungraded (consensus n/a). A KPI row should have been swapped for the "
                         "next-ranked KPI (SKILL.md, kpiSubstitutions).\n" % (_tk, _n))
    _snap_rows = normalise_snapshot(cfg)
    if _snap_rows:
        cfg = dict(cfg)
        cfg["snapshot"] = dict(cfg.get("snapshot") or {})
        cfg["snapshot"]["rows"] = _snap_rows
    _enforce("IDENTITY GATE", _tk, identity_contract(cfg), scope="identity")
    _enforce("STALENESS GATE", _tk, staleness_contract(cfg))
    _gcf, _gcw = graded_column_gate(cfg)
    _enforce("GRADED-COLUMN GATE", _tk, _gcf)
    for _w in _gcw:
        sys.stderr.write("WARN graded_column " + _tk + ": " + _w + "\n")
    _enforce("POLARITY GATE", _tk, polarity_gate(cfg), scope="polarity")
    for _bn, _bs in basis_band_rows(cfg):
        sys.stderr.write("WARN basis %s: row %r prints a %+.0f%% surprise, beyond the %.0f%% "
                         "band. Disclosed in the Section 4 caption; confirm the two sides are "
                         "on the same basis.\n" % (_tk, _bn, _bs, BASIS_BAND_PCT))
    _prim = str(cfg.get("primaryKpi") or "").strip()
    if _prim:
        _plt = _probe_ltcodes(cfg).get(_prim, "")
        if _row_is_inverted(cfg, _prim, _plt) or _reads_lower_is_better(_prim, _canon_metric):
            sys.stderr.write(
                "WARN primary_kpi %s: the mechanically selected Primary KPI %r is a "
                "lower-is-better / cost line. That is a legitimate outcome of the tie-break "
                "and must NOT be overridden, but it leads the Event Snapshot and the KPI "
                "hierarchy, so Section 6 has to say in one line why this cost is the driver "
                "of the quarter. If it is not, the tagged universe was thin - record the tier "
                "you used in `kpiProvenance` and say so in `kpiCap`.\n" % (_tk, _prim))
    _piw, _pinv = _polarity_inventory(cfg)
    for _w in _piw:
        sys.stderr.write("WARN polarity " + _tk + ": " + _w + "\n")
    if _pinv:
        sys.stderr.write("NOTE polarity " + _tk + ": " + _pinv + "\n")
    kpi_gate_check(cfg)
    _pf, _pw = spine_gate(cfg)
    _enforce("SPINE GATE", _tk, _pf, scope="spine_gate")
    for _w in _pw:
        sys.stderr.write("WARN spine_gate " + _tk + ": " + _w + "\n")
    _bf, _bw, _thin = broker_check(cfg)
    _enforce("BROKER-COUNT GATE", _tk, _bf, scope="broker_count")
    _enforce("BROKER-DISCLOSURE LINT", _tk, broker_prose_gate(cfg))
    _sf, _sw = sector_gate(cfg)
    _enforce("SECTOR GATE", _tk, _sf, scope="sector_gate")
    for _w in _sw:
        sys.stderr.write("WARN sector_gate " + _tk + ": " + _w + "\n")
    _cg = consensus_gate(cfg)
    if _cg:
        raise SystemExit("CONSENSUS-PROBE GATE FAIL " + str(cfg.get("ticker","")) + ":\n  - "
                         + "\n  - ".join(_cg))
    _enforce("GUIDANCE-FRESHNESS GATE", _tk, guidance_freshness_gate(cfg))
    _enforce("GUIDANCE-CONSENSUS GATE", _tk, guidance_consensus_gate(cfg))
    _enforce("TABLE-CONTRACT GATE", _tk, table_contract(cfg), scope="table_contract")
    _enforce("VALUATION SANITY GATE", _tk, valuation_sanity(cfg), scope="valuation_sanity")
    _enforce("VALUATION FX GATE", _tk, valuation_fx_basis(cfg), scope="valuation_sanity")
    _enforce("TABLE-FIGURES GATE", _tk, table_figures_gate(cfg))
    _enforce("PRICE-BASIS GATE", _tk, price_basis(cfg), scope="price_basis")
    _enforce("NOTE-CONTRACT GATE", _tk, note_contract(cfg), scope="note_contract")
    for _bm in basis_mismatch_advisory(cfg, _tk):
        print(_bm, file=sys.stderr)
    _enforce("RELEVANCE GATE", _tk, relevance_contract(cfg), scope="relevance")
    _enforce("KPI-NESTING GATE", _tk, kpi_nesting(cfg), scope="kpi_nesting")
    _enforce("CURRENCY GATE", _tk, one_currency_per_table(cfg), scope="one_currency")
    logo_path=os.path.join(HERE,"..","assets",LOGO_FILE)
    with open(logo_path,"rb") as f: logo=base64.b64encode(f.read()).decode()
    _MARK_W, _MARK_H = MARK_PX_W, MARK_PX_H
    _MARK_PT = _MARK_H * 0.75
    _ALPHA = 0.5681
    _BETA  = 1.0 - _ALPHA
    _BRAND_PT = 16.5
    _BRAND_PT_2L = 12.0
    _LADDER = [14.0, 13.5, 13.0, 12.5, 12.0]
    _TWO_PT = 10.0
    _L_BRAND = 17.0
    _L_BRAND_2L = 12.5
    _BUDGET_PT = 540.0 * 0.5524 - 2 * 7 * 0.75 - 2.0
    _tk = f'({cfg["ticker"]})'
    _sfx = (SUFFIX_TEMPLATE.replace("{TICKER}", str(cfg["ticker"]))
                           .replace("{PERIOD}", str(cfg["quarter"])))
    _coname = str(cfg["company"]).strip()
    _full = _coname + _sfx
    _co_pt = _LADDER[-1]
    for _s in _LADDER:
        _co_pt = _s
        if _fits(_full, _s, _BUDGET_PT): break
    _two = not _fits(_full, _co_pt, _BUDGET_PT)
    if _two:
        _co_pt = _TWO_PT
        _l1, _l2 = _balanced_split(_full, _TWO_PT, _BUDGET_PT * 0.95)
        _lines = [_l1, _l2]
        _brand_pt = _BRAND_PT_2L
        _l_brand = _L_BRAND_2L
    else:
        _lines = [_full]
        _brand_pt = _BRAND_PT
        _l_brand = _L_BRAND
    _n = len(_lines)
    _l_co = ((_MARK_PT - _BETA * _brand_pt - _ALPHA * _co_pt - _l_brand / 2.0)
             / (_n - 0.5)) + _CO_TRIM
    _mark_up = _l_co / 2.0 - _ALPHA * _co_pt + _MARK_TRIM
    parts=[]
    parts.append(
      '<table class="mast"><tr>'
      f'<td class="logo"><img style="width:{_MARK_W}px;height:{_MARK_H}px;'
      f'margin-bottom:{_mark_up:.3f}pt" src="data:image/png;base64,{logo}"></td>'
      '<td class="mid">'
      f'<div class="brand" style="font-size:{_brand_pt}pt;line-height:{_l_brand:.3f}pt">'
      f'<span class="i">{esc(BRAND_ORANGE_WORD)}</span>'
      f'<span class="a">{esc(BRAND_INK_WORD)}</span></div>'
      + "".join(
          f'<div class="co" style="font-size:{_co_pt}pt;line-height:{_l_co:.3f}pt">'
          f'{_co_html(_ln, _tk)}</div>' for _ln in _lines)
      + '</td>'
      '<td class="rt">'
      '<div class="ew">Earnings Wrap</div>'
      f'<div class="pr"><b>Prepared: </b>{esc(cfg["prepared"])}</div>'
      f'<div class="pr"><b>Reported: </b>{esc(cfg["reported"])}</div>'
      '</td></tr></table>')
    parts.append('<div class="orule"></div>')
    ex=['<div class="ex"><h3>EXECUTIVE SUMMARY</h3>',
        f'<div class="res"><b>Result: </b><b style="color:{_result_color(cfg)}">{esc(cfg["result"])}</b></div>']
    for label,items in cfg["summary"]:
        if isinstance(items,str): bullets(items,"summary")
        ex.append(f'<div class="exlabel">{esc(label.upper())}</div>')
        ex.append(bullets(items))
    ex.append('</div>')
    parts.append("".join(ex))
    parts.append(secH("2","Variant View"))
    for label,items in cfg["variant"]:
        if isinstance(items,str): bullets(items,"variant")
        parts.append(sub(label)); parts.append(bullets(items))
    parts.append(secH("3","Event Snapshot"))
    _sn=cfg["snapshot"]
    _resmap={r["name"]: r for _s in cfg.get("results",[]) for r in _s.get("rows",[])}
    if isinstance(_sn,dict) and _sn.get("head"):
        parts.append(eventSnapshot(_sn, _resmap, cfg))
    else:
        parts.append(tbl(["Metric","Value"],_sn,[4200,5640]))
    parts.append(cap(cfg.get("snapshotCap")))
    parts.append(secH("4","Results versus Expectations"))
    _cad = _cadence(cfg.get("quarter"))
    if _cad is None:
        sys.stderr.write("WARN results table: period label %r is not a recognised cadence "
                         "(NQnn, NHnn or YYYY), so the sequential column is headed "
                         "'Prior Period' rather than naming the cadence. Y/Y is unaffected.\n"
                         % (cfg.get("quarter"),))
    elif _cad == "annual" and any(r.get("pq") is not None
                                 for sec in (cfg.get("results") or ())
                                 for r in (sec.get("rows") or ())):
        sys.stderr.write("WARN results table: this is an ANNUAL filer, so the prior period IS "
                         "the year-ago period and the sequential column repeats Y/Y under a "
                         "second heading. Leave `pq` null on an annual filer.\n")
    parts.append(resultsTable(cfg["results"], _polarity_set(cfg), _seq_head(cfg.get("quarter")),
                              cfg=cfg, ltcodes=_probe_ltcodes(cfg)))
    parts.append(cap(_results_cap(cfg)))
    parts.append(takeaway(cfg["takeaway"]))
    parts.append(secH("5","What Drove the Quarter"))
    parts.append(bullets(cfg["drove"]))
    parts.append(secH("6","KPI Hierarchy and Performance"))
    for label,items in cfg["kpiHier"]:
        if isinstance(items,str): bullets(items,"kpiHier")
        parts.append(sub(label)); parts.append(bullets(items))
    _kt=cfg["kpiTable"]
    if _kt.get("quarters") and _kt.get("rows") and isinstance(_kt["rows"][0],dict):
        parts.append(kpiHistoryTable(_kt, _polarity_set(cfg), _polarity_noinv(cfg),
                                     shared_results_dp(cfg)))
    else:
        parts.append(tbl(_kt["head"],_kt["rows"],_kt["w"]))
    parts.append(cap(_kpi_cap(cfg)))
    parts.append(secH("7","Guidance and Estimate Revisions"))
    parts.append(sub(cfg["guidanceTone"]))
    parts.append(bullets(cfg["guidance"]))
    if not cfg.get("guidanceTable") and cfg.get("forwardTable"):
        parts.append('<div class="keep">' + sub("Forward Expectations")
                     + tbl(cfg["forwardTable"]["head"], cfg["forwardTable"]["rows"],
                           cfg["forwardTable"].get("w"))
                     + cap(cfg.get("forwardTableCap")) + '</div>')
    if cfg.get("guidanceTable"):
        _gcap = GUIDANCE_BASIS.get(str(cfg.get("guidanceBasis") or "").strip(), "")
        if cfg.get("guidanceTableCap"):
            _gcap = (_gcap + " " if _gcap else "") + cfg["guidanceTableCap"]
        parts.append('<div class="keep">' + sub("Guidance")
                     + tbl(cfg["guidanceTable"]["head"], cfg["guidanceTable"]["rows"],
                           cfg["guidanceTable"]["w"])
                     + cap(_gcap) + '</div>')
    if cfg.get("revisions"):
        w=cfg.get("revisionsWindow"); parts.append(sub("Estimate Revisions"+(" ("+w+")" if w else "")))
        parts.append(revisionsTable(cfg["revisions"], cfg))
        parts.append(cap(cfg.get("revisionsCap")))
    parts.append('<div class="keep">'+secH("8","Management Commentary")+bullets(cfg["mgmt"])+'</div>')
    parts.append(secH("9","Key Monitoring Variables"))
    parts.append(monitorBullets(cfg["monitor"]))
    parts.append(secH("10","Valuation Context"))
    parts.append(bullets(cfg["valNote"]))
    parts.append(tbl(cfg["valTable"]["head"],cfg["valTable"]["rows"],cfg["valTable"]["w"]))
    parts.append(f'<div class="src">{esc(cfg["sources"])}{"" if "investment advice" in str(cfg["sources"]).lower() else " For informational purposes only. Not investment advice."}</div>')

    css=CSS.replace("__FIRM__",esc(FIRM_NAME)).replace("__FOOT__",esc(cfg["ticker"]+" "+cfg["quarter"]
                                   +" Earnings Wrap  |  build "+VERSION))
    for _k in sorted(PALETTE, key=len, reverse=True):
        css=css.replace("__"+_k+"__", PALETTE[_k])
    _left=re.findall(r"__[A-Z]+__", css)
    if _left:
        raise SystemExit("PALETTE GATE FAIL: unresolved stylesheet token(s) %s - every colour "
                         "in CSS must map to a key of PALETTE" % sorted(set(_left)))
    html=("<!DOCTYPE html><html><head><meta charset='utf-8'><style>"+css+
          "</style></head><body>"+"".join(parts)+"</body></html>")
    html_path=out_pdf.rsplit(".",1)[0]+".html"
    open(html_path,"w",encoding="utf-8").write(html)
    validation_check(html, str(cfg.get("ticker","")))
    try:
        from weasyprint import HTML
    except (ImportError, OSError) as _e:
        HTML, _wp_err = None, _e
    if HTML is None:
        _word_edition(html, html_path, out_pdf, _wp_err)
    else:
        doc = HTML(html_path).render()
        pages = len(doc.pages)
        if pages > 4:
            raise SystemExit(
                "PAGE-FIT GATE FAIL: %d pages (contract is ~3, hard max 4). Trim captions, table rows "
                "or bullets. If the body font fell back to a non-Calibri-metric face the note reflows "
                "wider: run `python references/preflight.py` to check." % pages)
        doc.write_pdf(out_pdf)
        if pages == 4:
            print("PAGE-FIT WARNING: 4 pages (target 3). Run references/preflight.py to rule out a "
                  "font fallback, then trim if the fonts are correct.")
    for _p, _t in prose_figure_advisory(html):
        sys.stderr.write("WARN prose figure %s does not match %s printed in a table. "
                         "Deliberate rounding is fine; a restatement slip is not.\n"
                         % (_p, _t))
    for _p, _t in prose_percentage_advisory(html):
        sys.stderr.write("WARN prose percentage %s carries a Y/Y, Q/Q or beat cue but sits far "
                         "from every percentage printed in the note (nearest %s). Reconcile it "
                         "against the table before shipping: a computed delta stated in prose "
                         "must equal the computed delta in the table.\n" % (_p, _t))
    return html_path

def _pdf_pages(path):
    try:
        data = open(path, "rb").read()
    except OSError:
        return None
    n = len(re.findall(rb"/Type\s*/Page(?![s\w])", data))
    return n or None


def _docx_to_pdf(docx_path, out_pdf):
    import shutil
    out_dir = os.path.dirname(os.path.abspath(out_pdf)) or "."
    for exe in ("soffice", "libreoffice"):
        path = shutil.which(exe)
        if not path:
            continue
        try:
            subprocess.run([path, "--headless", "--convert-to", "pdf", "--outdir", out_dir, docx_path],
                           capture_output=True, timeout=240)
        except (OSError, subprocess.SubprocessError):
            continue
        made = os.path.join(out_dir, os.path.splitext(os.path.basename(docx_path))[0] + ".pdf")
        if os.path.isfile(made):
            if os.path.abspath(made) != os.path.abspath(out_pdf):
                os.replace(made, out_pdf)
            return out_pdf, _pdf_pages(out_pdf), "LibreOffice"
    if os.name == "nt" and shutil.which("powershell"):
        ps = ("$ErrorActionPreference='Stop'; $w=New-Object -ComObject Word.Application; $w.Visible=$false; "
              "$w.DisplayAlerts=0; try { $d=$w.Documents.Open('%s',$false,$true); $n=$d.ComputeStatistics(2); "
              "$d.ExportAsFixedFormat('%s',17); $d.Close($false); Write-Output \"PAGES=$n\" } finally { $w.Quit() }"
              % (os.path.abspath(docx_path).replace("'", "''"), os.path.abspath(out_pdf).replace("'", "''")))
        try:
            p = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                               capture_output=True, text=True, timeout=240)
            m = re.search(r"PAGES=(\d+)", p.stdout or "")
            if os.path.isfile(out_pdf) and m:
                return out_pdf, int(m.group(1)), "Microsoft Word"
        except (OSError, subprocess.SubprocessError):
            pass
    try:
        from docx2pdf import convert
        convert(docx_path, out_pdf)
        if os.path.isfile(out_pdf):
            return out_pdf, _pdf_pages(out_pdf), "docx2pdf"
    except Exception:
        pass
    return None, None, None


def _word_edition(html, html_path, out_pdf, why):
    sys.path.insert(0, HERE)
    import docx_wrap
    out_docx = out_pdf.rsplit(".", 1)[0] + ".docx"
    docx_wrap.build(html, out_docx)
    ok, detail = docx_wrap.text_parity(html, out_docx)
    if not ok:
        os.remove(out_docx)
        raise SystemExit("DOCX PARITY FAIL: the Word edition does not carry exactly the validated "
                         "text (%s). Nothing was delivered; report this as a skill defect." % detail)
    if os.path.exists(out_pdf):
        os.remove(out_pdf)
    pdf, pages, how = _docx_to_pdf(out_docx, out_pdf)
    if pages and pages > 4:
        for p in (pdf, out_docx):
            if p and os.path.exists(p):
                os.remove(p)
        raise SystemExit("PAGE-FIT GATE FAIL: %d pages in the Word edition (contract is ~3, hard max "
                         "4). Trim captions, table rows or bullets." % pages)
    print("WEASYPRINT UNAVAILABLE (%s): the note passed every gate and layer, and the validated "
          "HTML is at %s." % (str(why).splitlines()[0][:120] if str(why) else type(why).__name__, html_path))
    print("WORD EDITION: %s - typeset from the validated HTML, text %s." % (out_docx, detail))
    if pdf:
        print("PDF: %s, made from the Word edition with %s (%s)." % (
            pdf, how, ("%d pages" % pages) if pages else "page count not readable"))
        if pages == 4:
            print("PAGE-FIT WARNING: 4 pages (target 3). Trim if the fonts are correct.")
        print("DELIVER: the PDF (and the .docx if the user wants an editable copy).")
    else:
        print("No PDF converter (LibreOffice or Microsoft Word) on this machine, so the page count is "
              "not measured. DELIVER: the .docx, and say plainly that the PDF could not be built here.")


def main():
    if len(sys.argv)<3:
        print("usage: render_wrap.py <config.json> <out.pdf>"); sys.exit(2)
    cfg=json.load(open(sys.argv[1],encoding="utf-8")); out=sys.argv[2]
    hp=render(cfg,out)
    if os.path.exists(out):
        print("wrote",out,"and",hp)
    else:
        print("wrote",out.rsplit(".",1)[0]+".docx","and",hp)

if __name__=="__main__":
    main()
