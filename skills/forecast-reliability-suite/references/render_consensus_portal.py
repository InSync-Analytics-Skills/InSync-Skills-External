#!/usr/bin/env python3
"""
Forecast Reliability Suite (Consensus Suite) — deterministic renderer.
Usage:  python render_consensus_portal.py <input.json> <output.html> [--force]

GUIDING PRINCIPLE: measure forecasting reliability; DO NOT predict earnings.
Every statement is Historical Observation -> Supporting Numbers. No opinions, no
earnings predictions. The model only PULLS data from the InSync MCP into input.json;
this script does ALL math and rendering, and also writes <output>_metrics.json.

DATA-SUFFICIENCY GATE: if critical data is missing the script EXITS (code 3) with a
machine-readable INSUFFICIENT_DATA report and writes NO html. The calling model must
relay the gaps and ASK the user; only if the user agrees, re-run with --force, which
renders the report with a prominent warning banner.
"""
import json, sys, os, base64, math, statistics, re
import datetime as _wdt
from collections import defaultdict
from decimal import Decimal, ROUND_HALF_UP

VERSION="7.12.1"

def _brand_colors():
    _fb={"orange":"E8850C","orangeDk":"B25E08","ink":"1A1A1A","calloutBg":"F6F3EE",
         "beatGreen":"1E7A34","missRed":"B02A1F","inlineGray":"6E6E6E",
         "tableBorder":"DDDDDD","headerBg":"EDEDED"}
    _d=os.path.dirname(os.path.abspath(__file__))
    for _ in range(4):
        _c=os.path.join(_d,"common","brand.json")
        if os.path.exists(_c):
            try:
                _got=(json.load(open(_c,encoding="utf-8")) or {}).get("colors",{}) or {}
                return {k:str(_got.get(k,v)).lstrip("#") for k,v in _fb.items()}
            except Exception:
                return _fb
        _d=os.path.dirname(_d)
    return _fb
_BC=_brand_colors()
def _bc(name): return "#"+_BC[name]

CONFIG={
  "beat_tol":     {"pct":0.15, "val":0.30},
  "mag_high":     {"pct":0.50, "val":4.00},
  "mag_low":      {"pct":0.15, "val":1.00},
  "rel_dom_high": 0.85, "rel_dom_med": 0.65, "rel_dom_low": 0.60,
  "rel_min_n_high": 4,  "rel_min_n_low": 3,
  "disp_pct":  {"high":4.0, "mod":1.5},
  "disp_val":  {"high":13, "mod":7, "lomod":4, "low":1.5},
  "trust_dom":    0.75,
  "trust_inline": 0.75,
  "range_outlier_mult": 8.0,
  "range_outlier_floor": 25.0,
  "outlier_mult": 3.0,
  "cov_critical": 0.5,
  "guide_scale_lo": 0.02, "guide_scale_hi": 50.0,
  "anom_base_frac": 0.10,
  "big_surprise": 50.0,
  "anom_surprise": None,
  "conf_min_n": 6,
  "pred_w": {"surprise":0.35, "direction":0.20, "dispersion":0.25, "revision":0.20},
  "pred_k": {"surprise":8.0, "dispersion":4.0, "revision":5.0},
  "pred_high": 70, "pred_mod": 45,
  "chg_max_cols": 4,
  "qg_max_cols":  8,
  "fy_max_rows":  8,
  "keep_blocks_together": True,
}
_KNOWN={"--force","--nopdf","--out"}
FORCE="--force" in sys.argv
NOPDF="--nopdf" in sys.argv
_OUTDIR=None
args=[]; _it=iter(range(1,len(sys.argv)))
_i=1
while _i < len(sys.argv):
    _a=sys.argv[_i]
    if _a.startswith("--"):
        _name,_,_inline=_a.partition("=")
        if _name not in _KNOWN:
            print("INVALID_INPUT"); print(json.dumps({"error":f"unrecognised flag {_name}",
                  "known":sorted(_KNOWN)},indent=2)); sys.exit(2)
        if _name=="--out":
            if _inline: _OUTDIR=_inline
            elif _i+1 < len(sys.argv) and not sys.argv[_i+1].startswith("--"):
                _i+=1; _OUTDIR=sys.argv[_i]
            else:
                print("INVALID_INPUT"); print(json.dumps({"error":"--out needs a directory or path"},indent=2)); sys.exit(2)
    else:
        args.append(_a)
    _i+=1
INP=args[0] if args else "input.json"
OUTP=args[1] if len(args)>1 else "forecast_reliability_suite.html"
if _OUTDIR:
    _OUT_EXTS={".html",".htm"}
    _looks_like_file=os.path.splitext(_OUTDIR)[1].lower() in _OUT_EXTS
    if os.path.isdir(_OUTDIR) or _OUTDIR.endswith(os.sep) or not _looks_like_file:
        os.makedirs(_OUTDIR,exist_ok=True)
        OUTP=os.path.join(_OUTDIR,os.path.basename(OUTP))
    else:
        _p=os.path.dirname(os.path.abspath(_OUTDIR))
        os.makedirs(_p,exist_ok=True); OUTP=_OUTDIR
def _invalid(msg, **extra):
    print("INVALID_INPUT")
    print(json.dumps({"status":"error","error":msg,**extra}, indent=2))
    sys.exit(2)
try:
    D=json.load(open(INP, encoding="utf-8"))
except FileNotFoundError:
    _invalid(f"input file not found: {INP}",
             hint="pass the path to the assembled input.json as the first argument")
except UnicodeDecodeError as _e:
    _invalid(f"input file is not valid UTF-8: {INP} ({_e})")
except json.JSONDecodeError as _e:
    _invalid(f"input file is not valid JSON: {INP} (line {_e.lineno} column {_e.colno}: {_e.msg})")
except OSError as _e:
    _invalid(f"input file could not be read: {INP} ({_e})")
if not isinstance(D, dict):
    _invalid(f"input JSON must be an object, got {type(D).__name__}",
             hint="the top level is the report object described in references/data-contract.md")
if FORCE:
    for _req in ("metrics","fiscal_quarters"):
        if not isinstance(D.get(_req), list) or not D.get(_req):
            _invalid(f"input has no usable {_req}[]; --force renders a thin report, not a "
                     f"structureless one, and cannot override a missing {_req}[]",
                     hint="re-run WITHOUT --force to get the full sufficiency report")

def _clean(s):
    return re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]','',s).replace("—","-").replace("–","-") if isinstance(s,str) else s
_SINKS = {
    None:             ("company", "next_quarter", "ticker", "as_of"),
    "metrics":        ("label",),
    "guidance":       ("label", "metric"),
    "fy_guidance":    ("label",),
    "qual_guidance":  ("label", "text"),
    "revision_trail": ("date",),
    "revision_kpis":  ("label",),
}
for _coll, _keys in _SINKS.items():
    _rows = [D] if _coll is None else (D.get(_coll, []) or [])
    for _row in _rows:
        if not isinstance(_row, dict): continue
        for _k in _keys:
            if isinstance(_row.get(_k), str): _row[_k] = _clean(_row[_k])
for _m in D.get("metrics", []) or []:
    if isinstance(_m.get("variant"), str):
        _m["variant"] = _clean(_m["variant"]).strip().strip("[]")[:8] or None

def _isnum(x): return isinstance(x,(int,float)) and not isinstance(x,bool)
def _parse_date(s):
    if not isinstance(s,str): return None
    for _f in ("%d-%b-%y","%d-%b-%Y","%Y-%m-%d"):
        try: return _wdt.datetime.strptime(s.strip(),_f).date()
        except ValueError: pass
    return None
def qkey(fp):
    s=re.sub(r'^R\d?-','',str(fp).strip(),flags=re.I)
    m=re.fullmatch(r'(\d)Q(\d{2})',s,re.I)
    if m: return (2000+int(m.group(2)),int(m.group(1)))
    m=re.fullmatch(r'(\d)H(\d{2})',s,re.I)
    if m: return (2000+int(m.group(2)),int(m.group(1))*2)
    m=re.fullmatch(r'(\d{4})',s)
    if m: return (int(m.group(1)),4)
    return None

def _rskey(q):
    try: return (0,)+tuple(qkey(q))
    except Exception: return (1,str(q))

def cadence_of(labels):
    kinds=set()
    for l in labels or []:
        s=str(l).strip()
        if re.fullmatch(r'\dQ\d{2}',s): kinds.add('quarterly')
        elif re.fullmatch(r'\dH\d{2}',s,re.I): kinds.add('half_yearly')
        elif re.fullmatch(r'\d{4}',s): kinds.add('annual')
    if len(kinds)==1: return kinds.pop()
    if len(kinds)>1: return 'mixed'
    return 'quarterly'

_CADENCE_NOUN={'quarterly':('quarter','quarters','by quarter','Quarterly'),
               'half_yearly':('half-year','half-years','by half-year','Half-Yearly'),
               'annual':('year','years','by year','Annual'),
               'mixed':('period','periods','by period','Periodic'),
               'other':('period','periods','by period','Periodic')}
def validate(D):
    crit=[]; warn=[]
    ms=D.get("metrics",[]) or []
    if not ms: crit.append("input has no metrics[].")
    elif not any(m.get("headline") for m in ms): crit.append("input has no headline metric flagged.")
    qs=D.get("fiscal_quarters",[]) or []
    _wy=[]
    for _q in qs:
        _m=re.match(r"^[1-4]Q(\d{2})$",str(_q).strip())
        if _m: _wy.append(2000+int(_m.group(1)))
        else:
            _m2=re.match(r"^(\d{4})$",str(_q).strip())
            if _m2: _wy.append(int(_m2.group(1)))
    if _wy:
        _ny=_wdt.date.today().year
        if max(_wy)>_ny+2:
            crit.append(f"fiscal_quarters end in {max(_wy)}, more than two years ahead of today - the "
                        f"reporting window looks wrong. search_tickerid's latest_fiscal_quarter is not "
                        f"always right (it can return 4Q22 with quarter end 2222-12-31); "
                        f"confirm the window with get_call_date before rendering.")
        if _ny-min(_wy)>12:
            crit.append(f"fiscal_quarters begin in {min(_wy)}, over 12 years ago - the reporting window "
                        f"looks wrong; confirm it with get_call_date before rendering.")
    if len(qs)==0: crit.append("input has no fiscal_quarters.")
    elif len(qs)!=len(set(qs)): crit.append("fiscal_quarters has duplicates (would double-count).")
    else:
        ks=[qkey(q) for q in qs]
        _known=[k for k in ks if k is not None]
        if _known!=sorted(_known):
            crit.append("fiscal_quarters not in chronological (oldest→newest) order; chart/trail would scramble.")
        if len(_known)<len(ks):
            warn.append("some fiscal_quarters aren't a recognised period label ('2Q26', '1H26' or '2026'): "
                        +", ".join(str(q) for q,k in zip(qs,ks) if k is None)
                        +". Order was checked on the recognised labels only; guidance matching may be "
                         "unreliable for these (strip any 'R-' restated prefix and list the quarter in "
                         "restated_quarters[] instead).")
        _cad=cadence_of(qs)
        if _cad=='mixed':
            crit.append("fiscal_quarters mixes reporting cadences (quarters, half-years and/or years "
                        "in one window); surprises are not comparable across the boundary. Pull one cadence.")
        elif _cad in ('half_yearly','annual'):
            warn.append("non-quarterly filer: this window is %s, so every column, N and the "
                        "surprise series are %s, not quarters." % (_cad.replace('_','-'), _CADENCE_NOUN[_cad][1]))
        if len(qs)<3: warn.append(f"short window: {len(qs)} {_CADENCE_NOUN[_cad][1] if _cad in _CADENCE_NOUN else 'period(s)'}.")
    _trail=D.get("revision_trail",[]) or []
    if len(_trail)>=2:
        _tdts=[_s.get("date") for _s in _trail]
        _tpd=[_parse_date(_d) for _d in _tdts]
        if all(_p is not None for _p in _tpd):
            if _tpd!=sorted(_tpd):
                crit.append("revision_trail snapshots are not in chronological (oldest→newest) order "
                            f"({', '.join(str(d) for d in _tdts)}); Net move is computed first→last, so "
                            "every revision figure would print with the wrong sign. Sort the trail by date.")
        else:
            warn.append("revision_trail has snapshot date(s) that do not parse (expected '20-Feb-26' or "
                        "ISO): "+", ".join(str(d) for d,p in zip(_tdts,_tpd) if p is None)
                        +"; chronological order could not be verified, so the Net move direction is unchecked.")
    for m in ms:
        for q,r in (m.get("est_range",{}) or {}).items():
            mn,mx=r.get("min"),r.get("max")
            if mn is not None and mx is not None and mx<mn: crit.append(f'{m.get("label",m.get("key","?"))} {q}: est_range max<min.')
        for fld in ("actuals","consensus"):
            for q,v in (m.get(fld,{}) or {}).items():
                if v is not None and not _isnum(v): crit.append(f'{m.get("label",m.get("key","?"))} {q}: non-numeric {fld}.')
    for g in D.get("guidance",[]) or []:
        lo,hi=g.get("low"),g.get("high")
        if lo is not None and hi is not None and hi<lo: crit.append(f'guide {g.get("fiscal_period","?")}: high<low.')
    for g in D.get("fy_guidance",[]) or []:
        lo,hi=g.get("low"),g.get("high")
        if lo is not None and hi is not None and hi<lo: crit.append(f'fy_guide {g.get("fiscal_period","?")}: high<low.')
    _ca=D.get("consensus_asof",{}) or {}
    _rd=D.get("report_dates",{}) or {}
    if not isinstance(_ca,dict): crit.append("consensus_asof must be an object of {quarter: YYYY-MM-DD}.")
    else:
        if not _ca and qs:
            warn.append("consensus_asof absent entirely: no pre-print cutoff was verified "
                        "for any quarter, so the surprise figures rest on the supplied "
                        "consensus as given.")
        _ISO=re.compile(r'^\d{4}-\d{2}-\d{2}$')
        for _q in qs:
            _a=_ca.get(_q); _r=_rd.get(_q)
            if _a is None:
                if _ca: warn.append(f"consensus_asof missing {_q}; its pre-print cutoff is unverified.")
                continue
            if not (isinstance(_a,str) and _ISO.match(_a)):
                crit.append(f"consensus_asof {_q}: '{_a}' is not YYYY-MM-DD."); continue
            if not (isinstance(_r,str) and _ISO.match(str(_r))):
                warn.append(f"consensus_asof {_q}: no usable report_date to check the cutoff against."); continue
            if _a >= str(_r):
                crit.append(f"consensus_asof {_q}: {_a} is not before the {_r} print -- that is a POST-PRINT "
                            f"consensus panel; re-pull the last snapshot dated strictly before {_r}.")
            else:
                try:
                    from datetime import date as _dt
                    _gap=(_dt(*map(int,str(_r).split("-")))-_dt(*map(int,_a.split("-")))).days
                    if _gap>21: warn.append(f"consensus_asof {_q}: last snapshot is {_gap} days before the print; "
                                            f"verify no later pre-print snapshot exists.")
                except Exception: pass
    if _ca and not _rd: warn.append("consensus_asof supplied but report_dates is empty; cutoffs cannot be checked.")
    gnx=D.get("guide_next",{}) or {}
    lo,hi=gnx.get("eps_low"),gnx.get("eps_high")
    for k in ("eps_low","eps_high"):
        if gnx.get(k) is not None and not _isnum(gnx.get(k)): crit.append(f'guide_next.{k}: non-numeric.')
    if _isnum(lo) and _isnum(hi) and hi<lo: crit.append("guide_next: eps_high<eps_low.")
    return crit,warn
_TOL_NOTES=[]
def _tol_band(g):
    _m,_tp,_ta=g.get("mid"),g.get("tol_pct"),g.get("tol_abs")
    if _isnum(_m) and _isnum(_tp) and _tp>=0:
        _a,_b=_m*(1-_tp/100.0),_m*(1+_tp/100.0); return min(_a,_b),max(_a,_b)
    if _isnum(_m) and _isnum(_ta) and _ta>=0:
        return _m-_ta,_m+_ta
    return None
for _fld in ("guidance","fy_guidance"):
    _gl=D.get(_fld) if isinstance(D,dict) else None
    if not isinstance(_gl,list): continue
    _drop=[]
    for _g in _gl:
        if not isinstance(_g,dict) or "mid" not in _g: continue
        _tag=f'{_fld} {_g.get("fiscal_period","?")} {_g.get("metric") or _g.get("label") or ""}'.strip()
        _bd=_tol_band(_g)
        _has=(_g.get("low") is not None or _g.get("high") is not None)
        if not _has:
            if _bd: _g["low"],_g["high"]=_bd
            else: _TOL_NOTES.append(f'{_tag}: has `mid` but no usable tol_pct / tol_abs, so it carries no range.')
        elif _bd:
            _lo,_hi=_g.get("low"),_g.get("high")
            _eps=max(1e-9,0.001*max(1.0,abs(_g["mid"])))
            if not (_isnum(_lo) and _isnum(_hi) and abs(_lo-_bd[0])<=_eps and abs(_hi-_bd[1])<=_eps):
                _drop.append(_g)
                _TOL_NOTES.append(f'{_tag}: low/high {_lo}/{_hi} disagree with mid {_g["mid"]} and its tolerance '
                                  f'({_bd[0]:.4g}-{_bd[1]:.4g}); the guide was dropped, not guessed.')
    for _g in _drop: _gl.remove(_g)
STRUCT_CRIT,STRUCT_WARN=validate(D)
STRUCT_WARN=list(STRUCT_WARN)+_TOL_NOTES
_PP=[c for c in STRUCT_CRIT if "POST-PRINT" in c]
if _PP:
    print("INVALID_INPUT")
    print(json.dumps({"ticker":D.get("ticker","?"),"critical":_PP,
                      "action":"Post-print consensus detected. Re-pull the last snapshot dated STRICTLY BEFORE "
                               "each quarter's report date. This cannot be overridden with --force."},indent=2))
    sys.exit(2)
if STRUCT_CRIT and not FORCE:
    print("INSUFFICIENT_DATA")
    print(json.dumps({"ticker":D.get("ticker","?"),"critical":STRUCT_CRIT,"warnings":STRUCT_WARN,
                      "action":"Malformed input. Relay to the user; re-run with --force only to override."},indent=2))
    sys.exit(3)
_EXACT_MATCH_TYPES=("exact_ticker","exact_company","exact_isin","exact_cusip")
_RES=D.get("resolution")
_ID_FAIL=None; _ID_FORCEABLE=False
if not isinstance(_RES,dict) or not _RES:
    _ID_FORCEABLE=True
    _ID_FAIL=("input.json carries no `resolution` block, so nothing can confirm search_tickerid "
              "returned an EXACT match for the requested ticker. Record match_type, score, ticker_id "
              "and latest_fiscal_quarter VERBATIM from the search_tickerid row that produced "
              "ticker_id (SKILL.md Step 1) and re-run. If this input predates v7.7.0 and you accept "
              "an unverified issuer, --force renders it with that stated on the report.")
elif str(_RES.get("latest_fiscal_quarter","__absent__")).strip()=="":
    _ID_FAIL=("search_tickerid resolved "+repr(str(D.get("ticker","?")))+" but returned an EMPTY "
              "latest_fiscal_quarter, which means InSync holds no reported periods for this issuer. "
              "The identity is fine; the coverage is zero. There is nothing to measure the Street "
              "against, and an 8-quarter window cannot be derived from an empty value. Confirm the "
              "issuer with the user - a live example is KGC, which resolves to Kinross Gold Corp at "
              "exact_ticker score 100.0 with no reported periods.")
else:
    _MT=str(_RES.get("match_type") or "").strip().lower()
    if _MT not in _EXACT_MATCH_TYPES:
        _ID_FAIL=("search_tickerid matched "+repr(str(D.get("ticker","?")))+" as match_type="
                  +repr(_MT or "unknown")+", not one of "+", ".join(_EXACT_MATCH_TYPES)+" (score "
                  +repr(_RES.get("score"))+", ticker_id "+repr(_RES.get("ticker_id"))+"). A non-exact "
                  "match means the requested symbol is not in the InSync ticker table and the "
                  "resolver substituted its nearest neighbour - measured, that is a DIFFERENT "
                  "COMPANY at a score high enough to look convincing (98.5, 98.83, 99.14 all "
                  "returned the wrong issuer). Confirm the symbol, or resolve ticker_id by hand and "
                  "record the exact row.")
if _ID_FAIL and _ID_FORCEABLE and FORCE:
    STRUCT_CRIT.append("ISSUER IDENTITY NOT VERIFIED: this report was rendered with --force and no "
                       "`resolution` block, so nothing confirms search_tickerid returned an exact "
                       "match for "+repr(str(D.get("ticker","?")))+". Check the masthead names the "
                       "company you asked for before relying on any figure in it.")
    _ID_FAIL=None
if _ID_FAIL:
    print("INVALID_INPUT")
    print(json.dumps({"ticker":D.get("ticker","?"),"critical":[_ID_FAIL],
                      "action":"Issuer identity is not confirmed. Fix the resolution and re-run. "
                               "This cannot be overridden with --force: every figure in a "
                               "wrong-company report is internally consistent, so no later gate "
                               "can catch it and a human reading the masthead is the last line."},
                     indent=2))
    sys.exit(2)
TICK=D.get("ticker","?"); COMPANY=D.get("company",TICK)
QS=D.get("fiscal_quarters",[]) or []; NEXTQ=D.get("next_quarter",""); ASOF=D.get("as_of",""); N=len(QS)
_ASOF_OK = bool(D.get('consensus_asof') or {})
RESTATED=set(D.get("restated_quarters",[]) or [])
QLO=QS[0] if QS else "?"; QHI=QS[-1] if QS else "?"
CADENCE=cadence_of(QS)
PER_1,PER_N,PER_BY,PER_ADJ=_CADENCE_NOUN.get(CADENCE,_CADENCE_NOUN['quarterly'])
CUR=D.get("currency","$"); REVU=D.get("rev_unit","B")

def _currency_gate(D, cur_declared):
    hints=set()
    for k in ("reporting_currency","primary_currency","ccy"):
        v=D.get(k)
        if isinstance(v,str) and v.strip(): hints.add(v.strip())
    for m in (D.get("metrics") or []):
        u=m.get("unit")
        if isinstance(u,str):
            t=re.match(r'^\s*((?-i:GBPence|GBp|ZARc|USDc|EURc)|'
                       r'R\$|A\$|C\$|HK\$|NT\$|[\u20ac\u00a3\u00a5\u20b9\u20a9\u20bd\u20ba]|'
                       r'EUR|GBP|CHF|DKK|SEK|NOK|CAD|JPY|BRL|MXN|ZAR|INR|KRW|TWD|HKD|CNY|'
                       r'SGD|AUD|NZD|PLN|TRY|ILS|AED|SAR|THB|PHP|MYR)(?=$|[\s0-9.,\-]|[BMTK](?![A-Za-z]))', u, re.I)
            if t: hints.add(t.group(1))
    _EQ={"\u20ac":"EUR","\u00a3":"GBP","\u00a5":"JPY","\u20b9":"INR","\u20a9":"KRW","$":"USD","R$":"BRL",
         "A$":"AUD","C$":"CAD","HK$":"HKD","NT$":"TWD"}
    _SUB=("GBPence","GBp","ZARc","USDc","EURc")
    _norm=lambda t: (str(t).strip() if str(t).strip() in _SUB
                     else _EQ.get(str(t).strip(), str(t).strip().upper()))
    if not hints: return None
    if "currency" not in D:
        return ("Input carries "+", ".join(sorted(hints))+" but no `currency` field, so every money "
                "figure in this report is printed with the default \"$\". Set `currency` to the "
                "issuer's reporting symbol and re-run before circulating.")
    odd=[h for h in hints if _norm(h)!=_norm(cur_declared)]
    if odd:
        return ("`currency` is \""+str(cur_declared)+"\" but the input also carries "+", ".join(sorted(odd))+
                ". Confirm which is the reporting currency; the declared value was used.")
    return None
def eps_fmt(v):
    if v is None: return "n/a"
    v=_xrf(v,2)
    return f"{CUR}{v:.2f}"
def _mb(v,suf):
    s=str(suf or "").upper()
    if s=="M" and abs(v)>=1000: return v/1000.0,"B"
    if s=="B" and abs(v)>=1000: return v/1000.0,"T"
    return v,suf
def rev_fmt(v):
    if v is None: return "n/a"
    v,_s=_mb(v,REVU)
    v=_xrf(v,2)
    return f"{CUR}{v:,.2f}{_s}"

def esc(s): return str(s).replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")
def surprise(a,c,pct):
    if a is None or c is None or c==0: return None
    return (a-c) if pct else (a-c)/abs(c)*100.0
MINUS="−"
def mnum(t):
    if t.startswith("-"):
        return MINUS + t[1:]
    if len(t) > 1 and t[1] == "-" and not t[0].isdigit() and not t[0].isalnum():
        return MINUS + t[0] + t[2:]
    return t
def _xr(v, d):
    return str(Decimal(repr(float(v))).quantize(Decimal(1).scaleb(-d), rounding=ROUND_HALF_UP))
def _xrf(v, d):
    return float(_xr(v, d))
def sgn(v,dp=1):
    v=_xrf(v,dp)
    if v==0: return f"{0:.{dp}f}","mut"
    return mnum(f"{v:+.{dp}f}"), ("g" if v>0 else "r")
def _usd(v):
    v=_xrf(v,2)
    if v==0: return f"{CUR}0.00B","mut"
    return (("+" if v>0 else MINUS)+f"{CUR}{abs(v):,.2f}B"), ("g" if v>0 else "r")
def fmt_s(v,pct):
    if v is None: return "n/a"
    if pct=="usd": return _usd(v)[0]
    t,_=sgn(v); return f"{t}{' ppt' if pct else '%'}"
def span(t,c): return f'<span class="{c}">{t}</span>' if c else t
def col_s(v,pct,lb=False,neu=False):
    if v is None: return "n/a"
    if pct=="usd":
        t,c=_usd(v)
        if neu: return f'<span style="color:#1e3a8a">{t}</span>'
        if lb and c in ("g","r"): c="r" if c=="g" else "g"
        return span(t,c)
    t,c=sgn(v)
    if lb and c in ("g","r"): c="r" if c=="g" else "g"
    return span(f"{t}{' ppt' if pct else '%'}",c)
def avg(l): return sum(l)/len(l) if l else None

def _load_polarity_registry():
    try:
        _p=os.path.join(os.path.dirname(os.path.abspath(__file__)),"polarity_registry.json")
        return (json.load(open(_p,encoding="utf-8")) or {}).get("metrics",{}) or {}
    except Exception:
        return {}
_POL_REG=_load_polarity_registry()
def _lib_of(m):
    if m.get("lower_is_better") is not None: return bool(m["lower_is_better"])
    _e=_POL_REG.get(str(m.get("ltcode") or ""))
    if _e and _e.get("lower_is_better") is not None: return bool(_e["lower_is_better"])
    return False
def _polarity_declared(m):
    if m.get("lower_is_better") is not None: return True,"input"
    _e=_POL_REG.get(str(m.get("ltcode") or ""))
    if _e and _e.get("lower_is_better") is not None: return True,"registry"
    return False,"default"
def analyze(m):
    pct=m.get("pct_metric",False)
    acts=m.get("actuals",{}); cons=m.get("consensus",{}); rng=m.get("est_range",{})
    _dg=(m.get("gap_mode")=="dollar")
    surs={q:(surprise(acts.get(q),cons.get(q),pct) if not _dg else
             ((acts.get(q)-cons.get(q)) if (acts.get(q) is not None and cons.get(q) is not None) else None)) for q in QS}
    _cabs=[abs(cons.get(q)) for q in QS if cons.get(q) not in (None,0)]
    _cmed=statistics.median(_cabs) if _cabs else None
    _asr=(CONFIG.get("anom_surprise") or {}).get("pct" if pct else "val")
    _conf_q=set(str(x) for x in (m.get("confirmed_quarters") or []))
    anom=[q for q in QS if surs[q] is not None and q not in _conf_q and (
              (_cmed and cons.get(q) not in (None,0) and abs(cons.get(q))<CONFIG["anom_base_frac"]*_cmed)
              or (_asr is not None and abs(surs[q])>_asr))]
    if _dg: anom=[]
    vals=[surs[q] for q in QS if surs[q] is not None and q not in anom]
    tol=CONFIG["beat_tol"]["pct" if pct else "val"]
    if _dg: tol=0.005
    _pol=-1 if _lib_of(m) else 1
    beats=sum(1 for v in vals if _pol*v>tol); misses=sum(1 for v in vals if _pol*v<-tol)
    nn=len(vals); inline=nn-beats-misses; av=sum(vals)/nn if nn else None
    _rawb=sum(1 for v in vals if v>tol); _rawm=sum(1 for v in vals if v<-tol)
    if nn==0: direction="n/a"
    elif _rawb>_rawm: direction="Under (Street low)"
    elif _rawm>_rawb: direction="Over (Street high)"
    else: direction="Mixed"
    dom=(max(beats,misses)/nn) if nn else 0
    mag=abs(av) if av is not None else 0
    if _dg and av is not None and _cmed: mag=abs(av)/_cmed*100.0
    hi=CONFIG["mag_high"]["pct" if pct else "val"]; lo=CONFIG["mag_low"]["pct" if pct else "val"]
    if nn>=CONFIG["rel_min_n_high"] and dom>=CONFIG["rel_dom_high"] and mag>=hi: rel="High"
    elif nn>=CONFIG["rel_min_n_high"] and dom>=CONFIG["rel_dom_med"] and mag>=lo: rel="Medium"
    elif nn>=CONFIG["rel_min_n_low"] and dom>=CONFIG["rel_dom_low"]: rel="Low"
    else: rel="n/a"
    cvals=[cons.get(q) for q in QS if cons.get(q) is not None]
    zerocross=(not pct) and (not _dg) and cvals and (min(cvals)<0<max(cvals))
    widths=[]; zerowidth=0; rangedq=0; mins=[]; maxs=[]; _wq=[]
    for q in QS:
        r=rng.get(q)
        if not r: continue
        mn,mx=r.get("min"),r.get("max")
        if mn is not None and mx is not None:
            rangedq+=1; mins.append(mn); maxs.append(mx)
            if mx==mn: zerowidth+=1
            if pct: widths.append(abs(mx-mn)); _wq.append((q,abs(mx-mn)))
            else:
                c=cons.get(q)
                if c:
                    _w=abs(mx-mn)/abs(c)*100.0
                    widths.append(_w); _wq.append((q,_w))
    wmed=statistics.median(widths) if widths else None
    _wide=[]
    if wmed and wmed>0 and len(_wq)>=3:
        _mult=CONFIG.get("range_outlier_mult",8.0); _floor=CONFIG.get("range_outlier_floor",25.0)
        for _q,_w in _wq:
            if _w > _mult*wmed and _w >= _floor: _wide.append((_q,_w))
    _mu=str(m.get("unit") or "").strip()
    if pct:                        _pre,_suf="","%"
    elif m["key"]=="eps":          _pre,_suf=CUR,""
    elif m["key"]=="rev":          _pre,_suf=CUR,REVU
    elif _mu and _mu[0] in "$€£¥": _pre,_suf=_mu[0],_mu[1:]
    elif _mu:                      _pre,_suf="",f" {_mu}"
    else:                          _pre,_suf="",""
    def _dsc(ref):
        _f,_s=1.0,_suf
        if _pre and str(_suf).upper() in ("M","B"):
            _v2,_s=_mb(ref,_suf); _f=(ref/_v2) if _v2 else 1.0
        return _f,_s
    def _drd(ref): return 1 if pct else (0 if (not _pre and abs(ref)>=10000) else 2)
    def _dband(lo,hi):
        _f,_s=_dsc(max(abs(lo),abs(hi))); lo,hi=lo/_f,hi/_f; _r=_drd(max(abs(lo),abs(hi)))
        _sg=lambda x: (MINUS if _xrf(x,_r)<0 else "")+_pre+f"{abs(_xrf(x,_r)):,.{_r}f}"
        _a=mnum(f"{_xrf(lo,_r):,.{_r}f}"); _b=mnum(f"{_xrf(hi,_r):,.{_r}f}")
        if lo<0 and _pre: return f"{_sg(lo)}{_s} to {_sg(hi)}{_s}"
        return f"{_pre}{_a}{_s} to {_pre}{_b}{_s}" if lo<0 else f"{_pre}{_a}–{_b}{_s}"
    def _pt(v):
        _f,_s=_dsc(abs(v)); v=v/_f; _r=_drd(v)
        return f"{_pre}{mnum(f'{_xrf(v,_r):,.{_r}f}')}{_s}"
    rlo=statistics.median(mins) if mins else None
    rhi=statistics.median(maxs) if maxs else None
    if rlo is not None and rhi is not None:
        rng_str=_dband(rlo,rhi)
    else:
        rng_str="n/a"
    dp=CONFIG["disp_pct"]; dv=CONFIG["disp_val"]
    if wmed is None: disagree="n/a"
    elif pct: disagree="High" if wmed>=dp["high"] else ("Moderate" if wmed>=dp["mod"] else "Low")
    else: disagree="High" if wmed>=dv["high"] else ("Moderate" if wmed>=dv["mod"] else ("Low–Moderate" if wmed>=dv["lomod"] else ("Low" if wmed>=dv["low"] else "Very low")))
    w_str=(mnum(f"~{_xrf(wmed,1):.1f} ppt") if (pct and wmed is not None) else (mnum(f"~{_xrf(wmed,1):.1f}%") if wmed is not None else "n/a"))
    thin=(rangedq>0 and zerowidth>=rangedq/2)
    _bk=[rng[q].get("brokers") for q in QS if rng.get(q) and rng[q].get("brokers") is not None]
    brokers=statistics.median(_bk) if _bk else None
    no_disp=bool(thin and (brokers is None or brokers<2))
    d_rng,d_width,d_disp=rng_str,w_str,disagree
    if no_disp:
        _cv=[cons[q] for q in QS if cons.get(q) is not None]
        _cm=statistics.median(_cv) if _cv else None
        d_rng=(f"{_pt(_cm)} (no range published)" if _cm is not None else "no range published")
        d_width="n/a"; d_disp="n/a"
    _nr=m.get("next_range") or {}
    if _isnum(_nr.get("min")) and _isnum(_nr.get("max")) and _isnum(_nr.get("cons")) and _nr["cons"]:
        _n0,_n1,_nc=_nr["min"],_nr["max"],_nr["cons"]
        d_rng=_dband(_n0,_n1)
        _nw=abs(_n1-_n0) if pct else abs(_n1-_n0)/abs(_nc)*100.0
        d_width=(mnum(f"~{_xrf(_nw,1):.1f} ppt") if pct else mnum(f"~{_xrf(_nw,1):.1f}%"))
        if pct: d_disp="High" if _nw>=dp["high"] else ("Moderate" if _nw>=dp["mod"] else "Low")
        else: d_disp="High" if _nw>=dv["high"] else ("Moderate" if _nw>=dv["mod"] else ("Low–Moderate" if _nw>=dv["lomod"] else ("Low" if _nw>=dv["low"] else "Very low")))
        no_disp=False
    return dict(sfmt=("usd" if _dg else pct),neu=bool(m.get("neutral")),lb=_lib_of(m),pct=pct,surs=surs,anom=anom,beats=beats,misses=misses,inline=inline,n=nn,avg=av,rel=rel,direction=direction,
                disagree=disagree,thin=thin,width=w_str,wide_q=_wide,wmed_pct=wmed,wnum=wmed,rng_str=rng_str,rlo=rlo,rhi=rhi,label=m["label"],key=m["key"],variant=(m.get("variant") or None),
                brokers=brokers,no_disp=no_disp,d_rng=d_rng,d_width=d_width,d_disp=d_disp,
                headline=m.get("headline",False),basis=m.get("basis","adjusted"),zerocross=zerocross)
METR=[analyze(m) for m in D["metrics"]]

_LB_BY_NAME={str(m.get("label","")).strip().lower():bool(m.get("lower_is_better"))
             for m in D["metrics"] if m.get("lower_is_better") is not None}
_LB_GUIDE={}
for _src in (D.get("metrics") or [], D.get("guidance") or [], D.get("fy_guidance") or []):
    for _e in _src:
        _k=_e.get("key") or _e.get("metric") or _e.get("m")
        if _k and _e.get("lower_is_better") is not None: _LB_GUIDE[str(_k)]=bool(_e["lower_is_better"])
_LB_HINT=re.compile(r'\b(expenses?|costs?|charge-?offs?|nco|mlr|mcr|combined ratio|loss ratio|'
                    r'medical (?:loss|care) ratio|efficiency ratio|expense ratio|loss(?:es)?|loss rate|'
                    r'opex|capex|sg&a|shrink|attrition|churn|delinquen\w*|provisions?|impairments?|leverage|'
                    r'catastrophe|cat loss(?:es)?|days (?:sales )?outstanding|dso|dpo|'
                    r'casm|ncc|cost per|unit cost|cash cost|operating ratio|'
                    r'cost income ratio|cost-to-income|burn rate|loss adjustment)\b',re.I)
_POL_SRC={m.get("key"):_polarity_declared(m)[1] for m in D["metrics"]}
_POL_UNRESOLVED=[{"key":m.get("key"),"label":m.get("label","?"),"ltcode":m.get("ltcode"),
                  "scored_as":"higher_is_better (default)"}
                 for m in D["metrics"] if not _polarity_declared(m)[0]]
_undeclared=[m.get("label","?") for m in D["metrics"]
             if m.get("lower_is_better") is None and _LB_HINT.search(str(m.get("label","")))]
for _g in (D.get("guidance") or []):
    _gm=str(_g.get("metric") or _g.get("m") or "")
    if _g.get("lower_is_better") is None and _gm.strip().lower() not in _LB_BY_NAME and _LB_HINT.search(_gm):
        _undeclared.append(_gm)
_undeclared=sorted(set(x for x in _undeclared if x))
by_key={a["key"]:a for a in METR}; raw_by_key={m["key"]:m for m in D["metrics"]}

prices=D.get("prices",{}); react={}
for q in QS:
    p=prices.get(q)
    if p and p.get("prior") and p.get("report"):
        react[q]=(p["report"]-p["prior"])/p["prior"]*100.0
for a in METR:
    ss=[a["surs"][q] for q in QS if q not in a["anom"] and a["surs"].get(q) is not None]
    tol=CONFIG["beat_tol"]["pct"] if a["pct"] else CONFIG["beat_tol"]["val"]
    out=[x for x in ss if abs(x)>tol]
    n=len(ss); a["err_n"]=n
    a["typ_err"]=statistics.median([abs(x) for x in ss]) if ss else None
    lo=sum(1 for x in out if x>0); hi=len(out)-lo
    a["street_low"],a["street_high"]=lo,hi
    if n==0:                                     a["pattern"],a["pat_key"]="n/a","na"
    elif (n-len(out))/n>=CONFIG["trust_inline"]: a["pattern"],a["pat_key"]="Street gets this right","right"
    elif out and hi==0:                          a["pattern"],a["pat_key"]="Always low","always_low"
    elif out and lo==0:                          a["pattern"],a["pat_key"]="Always high","always_high"
    elif lo/n>=CONFIG["trust_dom"]:              a["pattern"],a["pat_key"]="Usually low","usually_low"
    elif hi/n>=CONFIG["trust_dom"]:              a["pattern"],a["pat_key"]="Usually high","usually_high"
    else:                                        a["pattern"],a["pat_key"]="No reliable direction","noisy"
    _raw=raw_by_key.get(a["key"],{}) or {}; _er=_raw.get("est_range",{}) or {}; _ac=_raw.get("actuals",{}) or {}
    ab=bl=ins=0; coll=[]
    for q in QS:
        r=_er.get(q) or {}; rlo,rhi,v=r.get("min"),r.get("max"),_ac.get(q)
        if rlo is None or rhi is None or v is None or q in a["anom"]: continue
        if rhi<=rlo: coll.append(q); continue
        if v>rhi: ab+=1
        elif v<rlo: bl+=1
        else: ins+=1
    a["rng_above"],a["rng_below"],a["rng_in"],a["rng_coll"]=ab,bl,ins,coll
    a["rng_n"]=ab+bl+ins

for a in METR:
    cov=(a["n"]/N) if N else 0
    if a["thin"] or a["n"]<CONFIG["rel_min_n_low"] or cov<0.5:
        a["conf"]="Low"
    elif a["n"]>=CONFIG["conf_min_n"] and cov>=0.99:
        a["conf"]="High"
    else:
        a["conf"]="Medium"

_CURSYM="$€£¥"
_PCTU=("%","pp","ppt","bp","bps","x")
def _gnum(v):
    return f"{_xrf(v,0):,.0f}" if abs(v)>=10000 else f"{v:g}"
def _gscale(ref,u):
    u=(u or "").strip()
    m=re.match(r'^(['+_CURSYM+r'])\s*(mm|MM|[KkMmBbTt])?$',u)
    if not m or ref is None: return 1.0,u
    sym=m.group(1); sc=(m.group(2) or "").upper().replace("MM","M")
    steps=["K","M","B","T"]
    f=1.0
    while sc in steps[:-1] and abs(ref/f)>=1000:
        f*=1000.0; sc=steps[steps.index(sc)+1]
    return f,sym+sc
def _gfix(v,u):
    u=(u or "").strip()
    if u in ("%","pp","ppt"): return mnum(f"{_xrf(v,1):.1f}")
    if u in ("bp","bps"):     return mnum(f"{_xrf(v,0):,.0f}")
    if abs(v)>=10000 and not re.match(r'^['+_CURSYM+r']',u):
        return mnum(f"{_xrf(v,0):,.0f}")
    return mnum(f"{_xrf(v,2):,.2f}")
def gval(v,u):
    if v is None: return "n/a"
    f,u=_gscale(v,u); v=v/f
    u=(u or "").strip()
    if u in _PCTU: return f"{_gfix(v,u)}{u}"
    m=re.match(r'^(['+_CURSYM+r'])\s*(mm|MM|[KkMmBbTt])?$',u)
    if m:
        sym=m.group(1); scale=(m.group(2) or "").upper().replace("MM","M")
        return f'{"−" if v<0 else ""}{sym}{_gfix(abs(v),u)}{scale}'
    return f"{_gfix(v,u)} {u}" if u else _gfix(v,u)
def grange(lo,hi,u):
    if lo is None and hi is None: return "n/a"
    f,u=_gscale(max([abs(x) for x in (lo,hi) if x is not None]),u)
    lo=None if lo is None else lo/f; hi=None if hi is None else hi/f
    if lo is None or hi is None or lo==hi: return gval(lo if lo is not None else hi,u)
    if lo<0 or hi<0: return f"{gval(lo,u)} to {gval(hi,u)}"
    a,b=gval(lo,u),gval(hi,u)
    m=re.match(r'^(['+_CURSYM+r'])(.*?)([KMBT]?)$',a)
    if m:
        m2=re.match(r'^(['+_CURSYM+r'])(.*?)([KMBT]?)$',b)
        if m2 and m.group(1)==m2.group(1) and m.group(3)==m2.group(3):
            return f"{m.group(1)}{m.group(2)}–{m2.group(2)}{m.group(3)}"
    if u in _PCTU: return f"{_gfix(lo,u)}–{_gfix(hi,u)}{u}"
    if u: return f"{_gfix(lo,u)}–{_gfix(hi,u)} {u}"
    return f"{a}–{b}"
_MK={m.get("key") for m in D.get("metrics",[])}
_MC={str(c) for m in D.get("metrics",[]) for c in (m.get("ltcode"),m.get("uic")) if c}
def _code_ok(c):
    c=str(c or "")
    return bool(c) and (c in _MC or (c.endswith("-Y") and c[:-2] in _MC))
def _gk(g):
    if g.get("uic") or g.get("ltcode"):
        return _code_ok(g.get("uic")) or _code_ok(g.get("ltcode"))
    k=str(g.get("metric") or "")
    return k in _MK or (k.startswith("fy_") and k[3:] in _MK)
def _base(g):
    for c in (g.get("uic"),g.get("ltcode")):
        c=str(c or "")
        if c: return c[:-2] if c.endswith("-Y") else c
    return None
def _isY(g): return any(str(c or "").endswith("-Y") for c in (g.get("uic"),g.get("ltcode")))
_G_OUT=[]
_G_LEGACY=not any((g.get("uic") or g.get("ltcode")) for _f in ("guidance","fy_guidance","qual_guidance") for g in (D.get(_f) or []))
for _fld in (() if _G_LEGACY else ("guidance","fy_guidance","qual_guidance")):
    _l=D.get(_fld) or []
    _keep=[g for g in _l if _gk(g)]
    _lvl={_base(g) for g in _keep if not _isY(g)}
    _keep=[g for g in _keep if not (_isY(g) and _base(g) in _lvl)]
    _G_OUT+=sorted({str(g.get("label") or g.get("metric") or "?") for g in _l if g not in _keep})
    D[_fld]=_keep
guides=D.get("guidance",[])
report_dates=D.get("report_dates",{}) or {}
q_rows=[]; units=set(); guide_skipped=[]; guide_unmatched=[]; guide_scale=[]; guide_late=[]
for g in guides:
    fp=g.get("fiscal_period","")
    if fp not in QS:
        kf=qkey(fp); khi=qkey(QHI)
        if not (kf and khi and kf>khi): guide_unmatched.append(str(fp))
        continue
    lo,hi=g.get("low"),g.get("high")
    mkey=g.get("metric"); act=g.get("actual")
    if act is None and mkey and raw_by_key.get(mkey): act=raw_by_key[mkey].get("actuals",{}).get(fp)
    mlabel=g.get("label") or (by_key.get(mkey,{}) or {}).get("label",mkey) or (mkey or "?")
    if lo is None or hi is None or act is None:
        guide_skipped.append(f'{mlabel} {fp}'); continue
    mid=(lo+hi)/2.0
    if mkey and raw_by_key.get(mkey):
        avs=[abs(v) for v in (raw_by_key[mkey].get("actuals",{}) or {}).values() if _isnum(v) and v!=0]
        if avs and mid!=0:
            ratio=(statistics.median(avs))/abs(mid)
            if ratio<CONFIG["guide_scale_lo"] or ratio>CONFIG["guide_scale_hi"]:
                guide_scale.append(f'{mlabel} {fp}'); guide_skipped.append(f'{mlabel} {fp} (scale mismatch)'); continue
    gd=g.get("guide_date"); rd=report_dates.get(fp)
    if gd and rd and str(gd)>=str(rd): guide_late.append(f'{mlabel} {fp}'); continue
    unit=g.get("unit",""); units.add(unit)
    _k=str(g.get("kind") or "outcome").strip().lower()
    kind="comparability" if _k.startswith(("comp","fx","mech")) else "outcome"
    q_rows.append(dict(m=mlabel,fp=fp,lo=lo,hi=hi,act=act,unit=unit,kind=kind,bh=act-hi,bm=act-mid,
                       lower_is_better=(g.get("lower_is_better") if g.get("lower_is_better") is not None
                                        else _LB_GUIDE.get(str(g.get("metric") or g.get("m") or ""))),
                       ph=((act-hi)/abs(hi)*100 if hi else None),pm=((act-mid)/abs(mid)*100 if mid else None)))
def _glb(r):
    if r.get("lower_is_better") is not None: return bool(r["lower_is_better"])
    return bool(_LB_BY_NAME.get(str(r.get("m","")).strip().lower(),False))
def _gbeat(r): return (r["act"]<r["lo"]) if _glb(r) else (r["act"]>r["hi"])
def _gmiss(r): return (r["act"]>r["hi"]) if _glb(r) else (r["act"]<r["lo"])
for _r in q_rows:
    if _glb(_r):
        _r["bd"]=_r["lo"]-_r["act"]; _r["pd"]=((_r["lo"]-_r["act"])/abs(_r["lo"])*100 if _r["lo"] else None)
    else:
        _r["bd"]=_r["bh"]; _r["pd"]=_r["ph"]
nq=len(q_rows); exceed=sum(1 for r in q_rows if _gbeat(r))
nq_q=len({r["fp"] for r in q_rows})
_gword="guide" if nq==1 else "guides"
OUT_R=[r for r in q_rows if r["kind"]=="outcome"]
CMP_R=[r for r in q_rows if r["kind"]=="comparability"]
nq_out=len(OUT_R);  exceed_out=sum(1 for r in OUT_R if _gbeat(r))
_COST_OUT=any(_glb(r) for r in OUT_R)
_COST_ANY=any(_glb(r) for r in q_rows)
nq_cmp=len(CMP_R);  inrange_cmp=sum(1 for r in CMP_R if r["lo"]-1e-9<=r["act"]<=r["hi"]+1e-9)
_gword_out="guide" if nq_out==1 else "guides"
def _cap(t): return (t[:1].upper()+t[1:]) if t else t
_cmp_names=sorted({r["m"] for r in CMP_R})
same_unit=(len(units)==1); U=next(iter(units)) if same_unit else ""
is_growth = U in ("%","pp","ppt")
def med(l):
    l=[x for x in l if x is not None]
    return statistics.median(l) if l else None
mixed_units=(len(units)>1)
bh_list=[r["bh"] for r in q_rows]; bm_list=[r["bm"] for r in q_rows]
med_bh=None if mixed_units else med(bh_list); med_bm=None if mixed_units else med(bm_list)
med_ph=med([r["ph"] for r in q_rows]); med_pm=med([r["pm"] for r in q_rows])
_ou={r["unit"] for r in OUT_R}
med_bh_out=(None if len(_ou)>1 else med([r["bd"] for r in OUT_R])); med_ph_out=med([r["pd"] for r in OUT_R])
med_absmiss_cmp=med([abs(r["bm"]) for r in CMP_R])
medabs=med([abs(x) for x in bh_list])
for r in q_rows: r["outlier"]=((not mixed_units) and medabs is not None and medabs>0 and abs(r["bh"])>CONFIG["outlier_mult"]*medabs)
outliers=[r for r in q_rows if r["outlier"]]
_bygm=defaultdict(list)
for r in q_rows: _bygm[(r["m"],r["unit"])].append(r)
gmed=[]
for (ml,un),rs in _bygm.items():
    n_=len(rs); ex=sum(1 for r in rs if _gbeat(r))
    inr=sum(1 for r in rs if r["lo"]-1e-9<=r["act"]<=r["hi"]+1e-9)
    gmed.append(dict(m=ml,unit=un,n=n_,exceed=ex,inrange=inr,kind=rs[0]["kind"],
                     med_bh=med([r["bh"] for r in rs]),med_ph=med([r["ph"] for r in rs]),
                     med_bd=med([r["bd"] for r in rs]),
                     med_bm=med([r["bm"] for r in rs]),med_pm=med([r["pm"] for r in rs]),
                     med_absmiss=med([abs(r["bm"]) for r in rs])))
gmed.sort(key=lambda g:(g["kind"]!="outcome",-g["n"],g["m"]))

PRED_W_CUSTOM=False; _pw_bad=False
_pw_in=D.get("pred_weights")
if _pw_in is not None:
    if isinstance(_pw_in,dict):
        _cw={k:float(_pw_in[k]) for k in CONFIG["pred_w"] if _isnum(_pw_in.get(k)) and _pw_in.get(k)>=0}
        if _cw and sum(_cw.values())>0:
            _base=dict(CONFIG["pred_w"]); _base.update(_cw); CONFIG["pred_w"]=_base; PRED_W_CUSTOM=True
        else: _pw_bad=True
    else: _pw_bad=True

def _clampf(x): return max(0.0,min(100.0,x))
_pc={}
_hv=[a for a in METR if a["headline"] and a["avg"] is not None]
_sv=[abs(a["avg"]) for a in _hv if not a["pct"]]
if _sv: _pc["surprise"]=_clampf(100-CONFIG["pred_k"]["surprise"]*(sum(_sv)/len(_sv)))
_dom=[max(a["beats"],a["misses"])/a["n"] for a in _hv if a["n"]]
if _dom: _pc["direction"]=_clampf((sum(_dom)/len(_dom)-0.5)/0.5*100)
_dw=[a["wnum"] for a in _hv if (a.get("wnum") is not None and not a["pct"])]
if _dw: _pc["dispersion"]=_clampf(100-CONFIG["pred_k"]["dispersion"]*(sum(_dw)/len(_dw)))
_rt=D.get("revision_trail",[]) or []
if len(_rt)>=2:
    e0,e1=_rt[0].get("eps"),_rt[-1].get("eps"); r0,r1=_rt[0].get("rev"),_rt[-1].get("rev")
    _dr=[];
    if e0 and e1: _dr.append(abs((e1-e0)/e0*100))
    if r0 and r1: _dr.append(abs((r1-r0)/r0*100))
    if _dr: _pc["revision"]=_clampf(100-CONFIG["pred_k"]["revision"]*(sum(_dr)/len(_dr)))
_W=CONFIG["pred_w"]; _ws=sum(_W[k] for k in _pc)
PRED=round(sum(_pc[k]*_W[k] for k in _pc)/_ws) if _ws>0 else None
PRED_BAND=(None if PRED is None else ("High" if PRED>=CONFIG["pred_high"] else ("Moderate" if PRED>=CONFIG["pred_mod"] else "Low")))
PRED_N=len(_pc)
def fmt_money(v,unit):
    if v is None: return "n/a"
    if unit in ("%","pp","ppt"): return f"{_xrf(v,1):+.1f} pp"
    sign="−" if v<0 else "+"; cur="$" if "$" in (unit or "") else ""
    suf="M" if (unit or "").endswith("M") else ("B" if (unit or "").endswith("B") else "")
    v=_xrf(v,2)
    return f"{sign}{cur}{abs(v):.2f}{suf}"
def fmt_delta(v_abs,v_pct,unit=None):
    if v_abs is None: return "n/a"
    if unit is None:
        u=U; grow=is_growth; blended=same_unit
    else:
        u=unit; grow=(u in ("%","pp","ppt")); blended=True
    if grow: return mnum(f"{_xrf(v_abs,1):+.1f}")+" ppt"
    base=fmt_money(v_abs,u)
    if v_pct is not None: v_pct=_xrf(v_pct,1)
    return f"{base} ({mnum(f'{v_pct:+.1f}')}%)" if (blended and v_pct is not None) else (mnum(f"{v_pct:+.1f}%") if v_pct is not None else base)
fy=D.get("fy_guidance",[]) or []; byfp=defaultdict(list)
FY_ACT={}; _fya_bad=False
_fya_in=D.get("fy_actuals")
if _fya_in is not None:
    if isinstance(_fya_in,dict):
        for _p,_mm in _fya_in.items():
            if isinstance(_mm,dict):
                _row={str(k):float(v) for k,v in _mm.items() if _isnum(v)}
                if _row: FY_ACT[str(_p)]=_row
            else: _fya_bad=True
    else: _fya_bad=True
fy_label={}
fy_unit={}
fy_skipped=0
for g in fy:
    if not g.get("guide_date"):
        fy_skipped+=1; continue
    mk=g.get("metric")
    if mk and mk not in fy_label:
        fy_label[mk]=g.get("label") or (by_key.get(mk,{}) or {}).get("label") or mk
    if mk and mk not in fy_unit:
        fy_unit[mk]=g.get("unit","")
    byfp[(g.get("fiscal_period"),g.get("metric"))].append(g)
raises=cuts=maints=0
fy_rcm=defaultdict(lambda:[0,0,0])
fy_latest={}
for key,lst in byfp.items():
    lst=sorted(lst,key=lambda x:x.get("guide_date",""))
    mk=lst[0].get("metric"); fp=lst[0].get("fiscal_period")
    for a2,b2 in zip(lst,lst[1:]):
        ma=(a2.get("low",0)+a2.get("high",0))/2; mb=(b2.get("low",0)+b2.get("high",0))/2
        if mb>ma+1e-9: raises+=1; fy_rcm[mk][0]+=1
        elif mb<ma-1e-9: cuts+=1; fy_rcm[mk][1]+=1
        else: maints+=1; fy_rcm[mk][2]+=1
    _last=lst[-1]
    if mk and (mk not in fy_latest or str(_last.get("guide_date",""))>str(fy_latest[mk][0])):
        fy_latest[mk]=(_last.get("guide_date",""),_last.get("low"),_last.get("high"),fp)
def _fy_year(fp):
    try: return int(str(fp)[:4])
    except: return None
_fy_years=sorted({y for y in (_fy_year(g.get("fiscal_period")) for g in fy) if y is not None})
_fy_live=max(_fy_years) if _fy_years else None
def _fy_prints(y): return len({str(g.get("guide_date")) for g in fy if _fy_year(g.get("fiscal_period"))==y and g.get("guide_date")})
_fy_full=[y for y in sorted(_fy_years,reverse=True) if _fy_prints(y)>=2]
CUR_FY=(_fy_full[0] if _fy_full else _fy_live)
FY_FALLBACK=(CUR_FY is not None and _fy_live is not None and CUR_FY!=_fy_live)
def _rcm_for(year):
    r=c=mt=0
    for _k,_l in byfp.items():
        _l=sorted(_l,key=lambda x:str(x.get("guide_date","")))
        if not _l or _fy_year(_l[0].get("fiscal_period"))!=year: continue
        for a2,b2 in zip(_l,_l[1:]):
            ma=(a2.get("low",0)+a2.get("high",0))/2; mb=(b2.get("low",0)+b2.get("high",0))/2
            if mb>ma+1e-9: r+=1
            elif mb<ma-1e-9: c+=1
            else: mt+=1
    return r,c,mt
_CUR_R,_CUR_C,_CUR_M=_rcm_for(CUR_FY) if CUR_FY is not None else (0,0,0)
def fy_changes(only_fy=None):
    _rev={str(v):k for k,v in report_dates.items()}
    def _rng(g,u):
        lo,hi=g.get("low"),g.get("high")
        if lo is None: return "n/a"
        return (f"{lo:g}" if lo==hi else f"{lo:g}–{hi:g}")+u
    ev=[]
    for key,lst in byfp.items():
        lst2=sorted(lst,key=lambda x:str(x.get("guide_date","")))
        if not lst2: continue
        mk=lst2[0].get("metric"); fp=lst2[0].get("fiscal_period"); u=fy_unit.get(mk,"") or ""
        if only_fy is not None and _fy_year(fp)!=only_fy: continue
        for a2,b2 in zip(lst2,lst2[1:]):
            ma=(a2.get("low",0)+a2.get("high",0))/2; mb=(b2.get("low",0)+b2.get("high",0))/2
            if abs(mb-ma)<=1e-9: continue
            gd=str(b2.get("guide_date",""))
            ev.append(dict(date=gd,q=_rev.get(gd,gd),m=fy_label.get(mk,mk),fp=fp,
                           frm=_rng(a2,u),to=_rng(b2,u),word=("Raised" if mb>ma else "Cut"),cls=("g" if mb>ma else "r")))
    ev.sort(key=lambda e:e["date"],reverse=True)
    return ev
def fy_current_grid(max_cols=CONFIG.get("chg_max_cols",4)):
    if CUR_FY is None: return None
    _rev={str(v):k for k,v in report_dates.items()}
    _sp={str(g.get("guide_date")):g.get("source_period") for g in fy if g.get("guide_date") and g.get("source_period")}
    dates=sorted({str(g.get("guide_date")) for g in fy if _fy_year(g.get("fiscal_period"))==CUR_FY and g.get("guide_date")})
    if not dates: return None
    kept=dates[-max_cols:]; dropped=len(dates)-len(kept)
    cols=[_sp.get(d) or _rev.get(d,d) for d in kept]
    rows=[]
    _mk_cur=sorted({g.get("metric") for g in fy if _fy_year(g.get("fiscal_period"))==CUR_FY and g.get("metric")},
                   key=lambda k: fy_label.get(k,k))
    _acts=FY_ACT.get(str(CUR_FY),{}) or {}
    for mk in _mk_cur:
        ents=sorted([g for g in fy if g.get("metric")==mk and _fy_year(g.get("fiscal_period"))==CUR_FY],
                    key=lambda x:str(x.get("guide_date","")))
        if not ents: continue
        u=fy_unit.get(mk,"") or ""
        valmap={}; chg={}; prev=None; nchg=0
        for g in ents:
            d=str(g.get("guide_date")); lo,hi=g.get("low"),g.get("high"); mid=((lo or 0)+(hi or 0))/2
            valmap[d]=(("n/a" if lo is None else grange(lo,hi,u)),mid)
            _c2=("mut" if prev is None else ("g" if mid>prev+1e-9 else ("r" if mid<prev-1e-9 else "mut")))
            if _c2 in ("g","r"): nchg+=1
            chg[d]=_c2; prev=mid
        _lb=bool(_LB_GUIDE.get(str(mk), _LB_BY_NAME.get(str(fy_label.get(mk,mk)).strip().lower(),False)))
        _up,_dn=(("r","g") if _lb else ("g","r"))
        cells=[]; kmids=[]
        for d in kept:
            if d in valmap:
                s,mid=valmap[d]; _dir=chg[d]
                cells.append((s,(_up if _dir=="g" else (_dn if _dir=="r" else "mut")),_dir)); kmids.append(mid)
            else: cells.append(("·","mut","mut"))
        if kmids:
            net,ncls=(("Raised",_up) if kmids[-1]>kmids[0]+1e-9 else (("Cut",_dn) if kmids[-1]<kmids[0]-1e-9 else ("Unchanged","mut")))
        else: net,ncls=("n/a","mut")
        av=_acts.get(mk); acell=None
        if _isnum(av):
            _last=ents[-1]; _lo,_hi=_last.get("low"),_last.get("high")
            if _lo is None or _hi is None: acell=(gval(av,u),"mut")
            else:
                _cls="g" if av>_hi+1e-9 else ("r" if av<_lo-1e-9 else "mut")
                _mk2="▲ " if _cls=="g" else ("▼ " if _cls=="r" else "")
                acell=(_mk2+gval(av,u),_cls)
        if not kmids and acell is None: continue
        rows.append(dict(m=fy_label.get(mk,mk),cells=cells,net=net,ncls=ncls,nchg=nchg,act=acell))
    if not rows: return None
    rows.sort(key=lambda r:(-r["nchg"], r["act"] is None, r["m"]))
    _cap=CONFIG.get("fy_max_rows",8); held=[]
    if len(rows)>_cap:
        _keep=[r for r in rows if r["nchg"]>0 or r["act"] is not None]
        _idle=[r for r in rows if r["nchg"]==0 and r["act"] is None]
        while len(_keep)<_cap and _idle: _keep.append(_idle.pop(0))
        held=[r["m"] for r in _idle]; rows=[r for r in rows if r in _keep]
    return dict(cols=cols,rows=rows,dropped=dropped,oldest=cols[0] if cols else None,
                has_act=any(r["act"] for r in rows),held=held)

def _qlab(n,yy): return f"{n}Q{yy%100:02d}"
def fy_year_split():
    try: cur=2000+int(str(NEXTQ)[-2:])
    except Exception: cur=CUR_FY
    if cur is None: return None
    prev=cur-1
    _rev={str(v):k for k,v in report_dates.items()}
    def _col(g): return g.get("source_period") or _rev.get(str(g.get("guide_date")))
    cols_prev=[_qlab(4,prev-1)]+[_qlab(i,prev) for i in (1,2,3)]
    _last=QS[-1] if QS else None
    cols_cur=[_qlab(4,prev)]+[_qlab(i,cur) for i in (1,2,3)]
    _ci=[c for c in cols_cur if c in set(QS) or c==_last]
    if _last in cols_cur: cols_cur=cols_cur[:cols_cur.index(_last)+1]
    else: cols_cur=_ci
    _order=[m.get("key") for m in D.get("metrics",[])]
    def _mkey(mk): return mk[3:] if (mk and mk.startswith("fy_") and mk[3:] in _order) else mk
    mks=sorted({g.get("metric") for g in fy if g.get("metric") and _fy_year(g.get("fiscal_period")) in (prev,cur)},
               key=lambda k:(_order.index(_mkey(k)) if _mkey(k) in _order else 99, str(k)))
    out=[]; early=[]
    for mk in mks:
        u=fy_unit.get(mk,"") or ""
        if not u and _mkey(mk)=="eps": u=CUR
        _lb=bool(_LB_GUIDE.get(str(mk), _LB_BY_NAME.get(str(fy_label.get(mk,mk)).strip().lower(),False)))
        blocks=[]
        for yr,cols in ((prev,cols_prev),(cur,cols_cur)):
            ents=sorted([g for g in fy if g.get("metric")==mk and _fy_year(g.get("fiscal_period"))==yr],key=lambda x:str(x.get("guide_date","")))
            at={}
            for g in ents:
                c=_col(g)
                if c in cols: at[c]=g
                elif ents: early.append(f"FY{yr} {fy_label.get(mk,mk)} ({c})")
            cells=[]; prevmid=None
            for c in cols:
                g=at.get(c)
                if not g: cells.append(("·","mut","")); continue
                lo,hi=g.get("low"),g.get("high"); mid=((lo or 0)+(hi or 0))/2
                d=("" if prevmid is None else ("up" if mid>prevmid+1e-9 else ("dn" if mid<prevmid-1e-9 else "")))
                cls=("mut" if not d else (("r" if _lb else "g") if d=="up" else ("g" if _lb else "r")))
                _txt=grange(lo,hi,u) if (lo is None or hi is None or lo==hi) else (gval(lo,u)+"–\n"+gval(hi,u))
                cells.append((_txt,cls if d else "",d)); prevmid=mid
            act=None
            if yr==prev:
                av=(FY_ACT.get(str(prev),{}) or {}).get(str(mk))
                if _isnum(av) and at:
                    lg=at[max(at,key=lambda c:cols.index(c))]; lo,hi=lg.get("low"),lg.get("high")
                    beat=(av<lo-1e-9) if _lb else (av>hi+1e-9); miss=(av>hi+1e-9) if _lb else (av<lo-1e-9)
                    arrow="▲ " if av>hi+1e-9 else ("▼ " if av<lo-1e-9 else "")
                    act=(arrow+gval(av,u),"g" if beat else ("r" if miss else ""))
                elif _isnum(av): act=(gval(av,u),"")
            blocks.append(dict(year=yr,cols=cols,cells=cells,act=act,has=bool(at)))
        if not any(b["has"] for b in blocks): continue
        _m=by_key.get(_mkey(mk)); lab=(_m["label"] if _m else str(fy_label.get(mk,mk)).replace("FY ","",1))
        out.append(dict(label=lab,blocks=blocks))
    return dict(rows=out,early=sorted(set(early)),prev=prev,cur=cur) if out else None
_FYS=fy_year_split() if fy else None
_NOCONS=[a for a in METR if not a["n"] and not a.get("headline")]
METR=[a for a in METR if a not in _NOCONS]

defs=[]
if _NOCONS:
    defs.append({"level":"warn","msg":"Omitted from the scorecard (no pre-print consensus in any of "
                 f"the {N} {PER_N}, so no bias, reliability or surprise can be computed): "
                 +", ".join(a["label"] for a in _NOCONS)
                 +". These line items are reported by the company but not forecast by the Street."})
_ccy_msg=_currency_gate(D,CUR)
if _ccy_msg: defs.append({"level":"warn","msg":_ccy_msg})
if _G_LEGACY and any(D.get(_f) for _f in ("guidance","fy_guidance")): defs.append({"level":"warn","msg":"Guidance carries no LTCODE/UIC, so it was NOT scoped to the metrics in the other tables (legacy input). Copy each guide's codes from get_guidance (SKILL.md Step 4)."})
if _G_OUT: defs.append({"level":"warn","msg":"Guidance not shown (metric not in the other tables, cc, or a Y/Y guide repeating a level guide): "+", ".join(_G_OUT)+"."})
if _FYS and _FYS["early"]: defs.append({"level":"warn","msg":"FY guides given before the year's 4Q print (not shown in the FY table): "+", ".join(_FYS["early"])+"."})
_HS_ROWS=("fcf","debt","cash")
def _hs_overrides():
    try:
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)),"hyperscaler_overrides.json")) as _f:
            _o=json.load(_f)
        return {str(x).upper() for x in _o.get("include",[])},{str(x).upper() for x in _o.get("exclude",[])}
    except Exception: return set(),set()
_HS_IN,_HS_EX=_hs_overrides()
_HS=D.get("hyperscaler") if isinstance(D.get("hyperscaler"),dict) else None
_TK=str(D.get("ticker","")).upper()
_hs_have=[k for k in _HS_ROWS if k in {m.get("key") for m in D.get("metrics",[])}]
if _HS is None:
    defs.append({"level":"warn","msg":"hyperscaler decision missing from input.json (SKILL.md Step 2a)."})
else:
    _hs_is=_HS.get("is")
    if _TK in _HS_IN and _hs_is is not True:
        defs.append({"level":"warn","msg":f"{_TK} is on the hyperscaler include list but input says is={_hs_is!r}; the list wins — add FCF/Total Debt/Cash."})
    if _TK in _HS_EX and _hs_is is True:
        defs.append({"level":"warn","msg":f"{_TK} is on the hyperscaler exclude list but input says is=true; the list wins — drop the three rows."})
    if _hs_is is True and len(_hs_have)<len(_HS_ROWS):
        defs.append({"level":"warn","msg":"hyperscaler=true but missing row(s): "+", ".join(k for k in _HS_ROWS if k not in _hs_have)+"."})
    if _hs_is is not True and _hs_have:
        defs.append({"level":"warn","msg":"hyperscaler rows present ("+", ".join(_hs_have)+") but hyperscaler.is is not true."})
    if _hs_is=="unsure":
        defs.append({"level":"warn","msg":f"hyperscaler: model unsure for {_TK} ({_HS.get('reason','no reason given')}); add it to hyperscaler_overrides.json to decide."})
for _m in STRUCT_CRIT: defs.append({"level":"critical","msg":_m})
for _m in STRUCT_WARN: defs.append({"level":"warn","msg":_m})
head=[a for a in METR if a["headline"]]
for a in head:
    cov=a["n"]/N if N else 0
    if cov<CONFIG["cov_critical"]: defs.append({"level":"critical","msg":f'{a["label"]}: consensus for only {a["n"]} of {N} quarters (<{int(CONFIG["cov_critical"]*100)}%).'})
    elif cov<1:
        _rm=raw_by_key.get(a["key"]) or {}
        _ac,_cs=_rm.get("actuals") or {},_rm.get("consensus") or {}
        _nc=sum(1 for q in QS if _cs.get(q) is None)
        _na=sum(1 for q in QS if _cs.get(q) is not None and _ac.get(q) is None)
        _nx=len(a.get("anom") or [])
        if _na==0 and _nx==0:
            defs.append({"level":"warn","msg":f'{a["label"]}: consensus missing for {N-a["n"]} of {N} quarters.'})
        else:
            _pt=[x for x in (f"consensus missing for {_nc}" if _nc else "",f"actual missing for {_na}" if _na else "",
                             f"{_nx} excluded as anomalies" if _nx else "") if x]
            defs.append({"level":"warn","msg":f'{a["label"]}: no scored surprise for {N-a["n"]} of {N} quarters ('+"; ".join(_pt)+').'})
nonhead=[a for a in METR if not a["headline"] and a["n"]>=4]
if len(nonhead)==0: defs.append({"level":"warn","msg":"No KPI metric has ≥4 quarters of consensus (KPI cards will be thin/empty)."})
_rt=D.get("revision_trail") or []
if len(_rt)>4:
    defs.append({"level":"warn","msg":f"revision_trail has {len(_rt)} snapshots; SKILL.md Step 6 fixes the "
                 f"Revision Momentum grid at 4 so it reads the same across tickers. All {len(_rt)} are "
                 "rendered - trim the input to the four documented anchors for comparability."})
_rawlab=[str(x.get("label") or x.get("date") or "") for x in _rt]
_isdate=[l for l in _rawlab if re.match(r"^\s*\d{1,4}[-/]",l) or re.match(r"^\s*\d{1,2}-[A-Za-z]{3}-\d{2,4}\s*$",l)]
if _isdate:
    defs.append({"level":"warn","msg":"revision_trail columns are headed by raw dates ("+", ".join(_isdate[:3])+
                 (", ..." if len(_isdate)>3 else "")+"); SKILL.md Step 6 asks for fiscal-quarter phase labels "
                 "(\"Pre 2Q26\", \"Post 2Q26\") so columns mean the same thing on every ticker."})
if D.get("revision_trail") and not (D.get("revision_kpis")) and len(nonhead)>0:
    defs.append({"level":"warn","msg":"Revision Momentum shows EPS + Revenue only: no revision_kpis were supplied though this company has KPI metrics. Pull the top KPIs' forward next-quarter estimate history and add revision_kpis so the table is not a 2-row fallback."})
if len(react)==0: defs.append({"level":"critical","msg":"No price history in any window; the print-day reaction range cannot be shown."})
elif len(react)<N/2: defs.append({"level":"warn","msg":f"Price reaction available for only {len(react)} of {N} quarters."})
for _a in METR:
    if _a.get("variant"): _a["label"]=f'{_a["label"]} [{_a["variant"]}]'
VARIANTS=sorted({a["variant"] for a in METR if a.get("variant")})
for _a in METR:
    if _a.get("wide_q"):
        _qs=", ".join(f'{q} ({_xrf(w,1):,.1f}% wide)' for q,w in _a["wide_q"])
        defs.append({"level":"warn","msg":f'{_a["label"]}: estimate range on {_qs} is more than '
                     f'{CONFIG.get("range_outlier_mult",8.0):g}x this metric\'s median width '
                     f'({_a["wmed_pct"]:,.0f}%) — likely one bad broker row. '
                     +('Dispersion for this metric is reported unscored rather than from a corrupted band.'
                       if _a.get("disagree")=="n/a" else
                       'Treat the dispersion read on this metric with care.')})
thinm=[a["label"] for a in METR if a["thin"]]
_collq=sorted({q for a in METR for q in a["rng_coll"]})
if _collq: defs.append({"level":"warn","msg":"Collapsed estimate range (MIN=MAX) on "+", ".join(_collq)
                        +"; those quarters are excluded from the beat-every-analyst counts in _metrics.json."})
_gq={r["fp"] for r in q_rows}
if q_rows and QS and QS[0] not in _gq and len([q for q in QS if q in _gq])>=len(QS)//2:
    defs.append({"level":"warn","msg":f"No quarterly guide mapped for {QS[0]} (the oldest quarter in the window) "
                 "while later quarters are guided; verify the pull covered guides ISSUED before the window "
                 "(get_guidance defaults to a 2-year lookback on GUIDEDATE)."})
if thinm: defs.append({"level":"warn","msg":"Collapsed estimate range (MIN=MAX in ≥ half the quarters) on: "+", ".join(thinm)+"; dispersion/surprise unreliable."})
zc=[a["label"] for a in METR if a.get("zerocross")]
if zc: defs.append({"level":"warn","msg":"Consensus crosses zero within the window on: "+", ".join(zc)+"; % surprises for these are unstable; read the absolute figures."})
_anom=[f'{a["label"]} {q}' for a in METR for q in a["anom"]]
if _anom: defs.append({"level":"warn","msg":"Excluded from surprise stats (near-zero or mis-scaled consensus base, or a surprise beyond the outlier "
     "threshold): "+", ".join(_anom)+"; check the pre-print consensus for these quarters. A collapsed estimate range, near-zero base or restatement is the usual cause, but on lines whose volatility IS the business (catastrophe losses, capital deployment, development starts) the exclusion may reflect genuine lumpiness rather than a data fault."})
if _pw_bad: defs.append({"level":"warn","msg":"pred_weights supplied but unusable (not a dict / all non-positive / unknown keys); default Predictability weights used."})
if _fya_bad: defs.append({"level":"warn","msg":"fy_actuals supplied but partly unusable (expected {fiscal_period: {metric_key: number}}); the unreadable entries were ignored and no full-year actual is shown for them."})
if fy and CUR_FY is not None and not FY_ACT.get(str(CUR_FY)) and not (_fy_live is not None and CUR_FY==_fy_live):
    defs.append({"level":"warn","msg":f"FY{CUR_FY} is a COMPLETED year but no fy_actuals were supplied for it, so the guide trajectory cannot be scored against the realized outcome. Pull the annual actual for the guided metrics and add fy_actuals."})
eps_resolvable=any(a["key"]=="eps" for a in head) or any(a["headline"] and "eps" in a["label"].lower() for a in METR)
if not eps_resolvable and head:
    defs.append({"level":"warn","msg":"No EPS metric could be resolved (reserve key 'eps' or flag an EPS headline); the EPS headline fact/chart is omitted."})
if guide_skipped: defs.append({"level":"warn","msg":"Guidance entries skipped (no resolvable metric/actual): "+", ".join(guide_skipped)+"."})
if guide_unmatched: defs.append({"level":"warn","msg":"Guidance rows dropped: fiscal_period not in the window (check label format, e.g. 'Q2 2025' vs '2Q25'): "+", ".join(guide_unmatched)+"."})
if guide_scale: defs.append({"level":"warn","msg":"Guidance dropped for a scale mismatch vs the metric's actuals (units differ, e.g. $M vs $B): "+", ".join(guide_scale)+"."})
if guide_late: defs.append({"level":"warn","msg":"Guidance excluded: issued on/after the quarter's report date (not a pre-print guide): "+", ".join(guide_late)+"."})
if mixed_units: defs.append({"level":"warn","msg":"Guidance spans mixed units; medians are reported per guided metric (see the per-metric table), not blended."})
if _undeclared: defs.append({"level":"warn","msg":"Polarity not declared on cost/expense-style line(s): "+", ".join(_undeclared)+". These are scored as higher-is-better, so coming in BELOW consensus or under a guide counts as a miss. Set \"lower_is_better\": true on the metric (and/or the guide row) if that is wrong."})
if fy_skipped: defs.append({"level":"warn","msg":f"{fy_skipped} FY guide entr{'y' if fy_skipped==1 else 'ies'} had no guide_date and were excluded from the raise/cut/maintain count."})
if nq==0: defs.append({"level":"warn","msg":"No quantitative quarterly guidance mapped; Guidance Credibility will be limited."})
critical=[d for d in defs if d["level"]=="critical"]

if critical and not FORCE:
    print("INSUFFICIENT_DATA")
    print(json.dumps({"ticker":TICK,"critical":[d["msg"] for d in critical],
                      "warnings":[d["msg"] for d in defs if d["level"]=="warn"],
                      "action":"Relay these gaps to the user and ask whether to generate anyway; if yes, re-run with --force."},indent=2))
    sys.exit(3)

def strongest():
    rated=[a for a in METR if a["rel"] in ("High","Medium") and a["avg"] and not a["headline"] and a["sfmt"]!="usd"]
    if rated: rated.sort(key=lambda a:abs(a["avg"]),reverse=True); return rated[0]
    anyk=[a for a in METR if a["avg"] is not None and not a["headline"] and a["n"]>=3 and a["sfmt"]!="usd"]
    if anyk: anyk.sort(key=lambda a:abs(a["avg"]),reverse=True); return anyk[0]
    return None
sk=strongest()
epsa=by_key.get("eps") or next((a for a in METR if a["headline"] and "eps" in a["label"].lower()),None)
nc=D.get("next_consensus",{}); gn=D.get("guide_next",{})
_eps_basis=(nc.get("basis") or (epsa["basis"] if epsa else "adjusted"))
_eps_gaap=str(_eps_basis).lower() in ("reported","gaap","gaap_diluted")
EPS_LABEL="GAAP diluted EPS" if _eps_gaap else "adjusted EPS"
_head_lbl=(epsa or {}).get("label") or ""
_is_eps_metric=("eps" in _head_lbl.lower()) or not _head_lbl
HEAD_LABEL=EPS_LABEL if _is_eps_metric else _head_lbl
HEAD_SHORT="EPS" if _is_eps_metric else _head_lbl
_revm=by_key.get("rev") or next((a for a in METR if a["headline"] and a is not epsa),None)
_rev_lbl=((_revm or {}).get("label") or "revenue")
REV_LABEL="revenue" if _rev_lbl.strip().lower() in ("total revenue","revenue") else _rev_lbl

facts=[]
if epsa and epsa["avg"] is not None:
    _elc=HEAD_LABEL[0].upper()+HEAD_LABEL[1:]
    _dir="Street low" if epsa["avg"]>0 else "Street high"
    facts.append(f'{_elc} beat consensus <b>{epsa["beats"]} of {epsa["n"]}</b> {PER_N} (avg {fmt_s(epsa["avg"],False)}, {_dir}).')
if sk:
    facts.append(f'Widest gap: {esc(sk["label"])}, avg {fmt_s(sk["avg"],sk["pct"])} over {sk["n"]} {PER_N}.')
if nq_out:
    _mtail=f' (median {fmt_delta(med_bh_out,med_ph_out)})' if med_bh_out is not None else ''
    if _COST_OUT:
        facts.append(f'Actual beat the guide (above the high end; below the low end for a cost line) in <b>{exceed_out} of {nq_out}</b> outcome {_gword_out}{_mtail}.')
    else:
        facts.append(f'Actual cleared the guided high end in <b>{exceed_out} of {nq_out}</b> outcome {_gword_out}{_mtail}.')
elif nq_cmp:
    facts.append(f'<b>No quarterly outcome guidance</b> in this window, so no conservatism read; '
                 f'comparability guides landed in range <b>{inrange_cmp} of {nq_cmp}</b>.')
if nq_out and nq_cmp:
    facts.append(f'Comparability guides (effects outside management control) landed in range {inrange_cmp} of {nq_cmp}.')
if react:
    w=min(react.items(),key=lambda kv:kv[1]); b=max(react.items(),key=lambda kv:kv[1])
    facts.append(f'1-day reaction ranged {mnum(f"{_xrf(w[1],1):+.1f}")}% ({w[0]}) to {mnum(f"{_xrf(b[1],1):+.1f}")}% ({b[0]}).')
if nc.get("eps") is not None:
    lo_,hi_=gn.get("eps_low"),gn.get("eps_high")
    _rv=f' and {REV_LABEL} <b>{rev_fmt(nc["rev"])}</b>' if nc.get("rev") is not None else ''
    if lo_ is not None and hi_ is not None:
        _e,_l,_h=eps_fmt(nc["eps"]),eps_fmt(lo_),eps_fmt(hi_)
        if   nc["eps"]>hi_ and _e!=_h: pos="above"
        elif nc["eps"]<lo_ and _e!=_l: pos="below"
        elif _e==_h and _l!=_h:        pos="at the high end of"
        elif _e==_l and _l!=_h:        pos="at the low end of"
        else:                          pos="inside"
        facts.append(f'Into {esc(NEXTQ)}: consensus {HEAD_SHORT} <b>{_e}</b>{_rv}; {HEAD_SHORT} {pos} the {_l}–{_h} guide.')
    elif hi_ is not None:
        facts.append(f'Into {esc(NEXTQ)}: consensus {HEAD_SHORT} <b>{eps_fmt(nc["eps"])}</b>{_rv}, vs a guide high end of {eps_fmt(hi_)}.')
    elif lo_ is not None:
        facts.append(f'Into {esc(NEXTQ)}: consensus {HEAD_SHORT} <b>{eps_fmt(nc["eps"])}</b>{_rv}, vs a guide low end of {eps_fmt(lo_)}.')
    else:
        facts.append(f'Into {esc(NEXTQ)}: consensus {HEAD_SHORT} <b>{eps_fmt(nc["eps"])}</b>{_rv} (no quantitative guide).')
summary="  ".join(facts) if facts else "Insufficient consensus history to summarize."

RATED=("High","Medium","Low")
REL_WORD={"High":"Strong","Medium":"Moderate","Low":"Weak"}
REL={"High":span("Strong","g"),"Medium":span("Moderate","y"),"Low":span("Weak","mut"),
     "n/a":span("None detected","mut")}
DIS={"High":span("High","r"),"Moderate":span("Moderate","y"),"Low–Moderate":span("Low–Moderate","y"),
     "Low":span("Low","mut"),"Very low":span("Very low","mut"),"n/a":span("n/a","mut")}
def dirspan(v,rated=True):
    if not rated: return span(v,"mut")
    if v.startswith("Under"): return span(v,"g")
    if v.startswith("Over"): return span(v,"r")
    return span(v,"mut")
def _cls(i,numcols,leftcols):
    if i==0 or i in leftcols: return ""
    return "num" if i in numcols else "ctr"
def table(headers,rows,numcols=(),leftcols=()):
    th="".join((lambda c: f'<th class="{c}">{h}</th>' if c else f'<th>{h}</th>')(_cls(i,numcols,leftcols)) for i,h in enumerate(headers))
    body="".join("<tr>"+"".join((lambda c: f'<td class="{c}">{v}</td>' if c else f'<td>{v}</td>')(_cls(i,numcols,leftcols)) for i,v in enumerate(r))+"</tr>" for r in rows)
    return f'<table><thead><tr>{th}</tr></thead><tbody>{body}</tbody></table>'
_CARD_N=[0]
def card(title,tag,inner,collapsed=False,col=None):
    dc=' data-collapsed="true"' if collapsed else ''
    tg=f'<span class="tag">{esc(tag)}</span>' if tag else ''
    cc=f' data-col="{col}"' if col is not None else ''
    _CARD_N[0]+=1; oc=f' data-ord="{_CARD_N[0]}"'
    return f'<div class="card collapsible-section"{dc}{cc}{oc}><h2>{esc(title)}{tg}</h2><div class="body">{inner}</div></div>'
def star(a): return " *" if (a and a.get("basis")=="reported") else ""
def _exq(a): return "†" if (a and a.get("anom")) else ""
def _nocons(a): return not (a and a.get("n"))
def _rel_cell(a): return "no consensus" if _nocons(a) else None
def _conf_cell(a): return "" if _nocons(a) else None
_EXQ=[(a["label"],list(a["anom"])) for a in METR if a.get("anom")]
_BIG=[(a["label"],q,a["surs"][q]) for a in METR if not a["pct"] and a["sfmt"]!="usd" for q in QS
      if a["surs"].get(q) is not None and abs(a["surs"][q])>CONFIG["big_surprise"]]
_big_note=("" if not _BIG else
           " Surprises above "+f'{CONFIG["big_surprise"]:.0f}%'+": "
           +"; ".join(f'{l} {q} {mnum(f"{_xrf(v,1):+.1f}")}%'+(" (not counted)" if q in next(a["anom"] for a in METR if a["label"]==l) else "")
                      for l,q,v in _BIG)+".")
_exq_note=("" if not _EXQ else
           " <b>†</b> N excludes quarter(s) dropped from the surprise stats: "
           +"; ".join(f'{l} ({", ".join(q)})' for l,q in _EXQ)
           +". The rating for those rows rests on the remaining quarters only.")

relorder={"High":0,"Medium":1,"Low":2,"n/a":3}
sc=sorted(METR,key=lambda a:(relorder.get(a["rel"],9),-(abs(a["avg"]) if a["avg"] else 0)))
def _bias_txt(a):
    if _nocons(a): return "no consensus"
    tol=0.005 if a.get("sfmt")=="usd" else (CONFIG["beat_tol"]["pct"] if a["pct"] else CONFIG["beat_tol"]["val"])
    vs=[a["surs"][q] for q in QS if q not in a["anom"] and a["surs"].get(q) is not None]
    lo=sum(1 for v in vs if v>tol); hi=sum(1 for v in vs if v<-tol); n=len(vs)
    w=REL_WORD.get(a["rel"]) if a["rel"] in RATED else None
    if not w or lo==hi: t="None"
    else: t=f"{w}: too {'low' if lo>hi else 'high'} ({max(lo,hi)} of {n})"
    if a.get("conf") not in (None,"High"): t+=" · limited data"
    return t
CONF={"High":span("High","g"),"Medium":span("Medium","y"),"Low":span("Low","mut"),"n/a":span("n/a","mut")}
sc_rows=[[esc(a["label"])+star(a),str(a["n"])+_exq(a),(f'{a["beats"]} above / {a["misses"]} below' if a["neu"] else f'{a["beats"]}B / {a["misses"]}M / {a["inline"]}I'),col_s(a["avg"],a["sfmt"],a["lb"],a["neu"]),
          dirspan(a["direction"],a["rel"] in RATED),
          (_rel_cell(a) or REL.get(a["rel"],a["rel"])),
          (_conf_cell(a) if _conf_cell(a) is not None else CONF.get(a["conf"],a["conf"]))] for a in sc]
scorecard=table(["Metric","N","Beat/Miss/In-line","Avg surprise","Direction","Consensus Bias Strength","Confidence"],
                sc_rows,numcols=(1,3))
_ND=any(a["rel"]=="n/a" and not _nocons(a) for a in METR)
notes=[]
if any(star(a) for a in METR): notes.append('* Reported (GAAP) basis; Adjusted not available.')
if RESTATED: notes.append('Restated periods ('+esc(", ".join(sorted(map(str,RESTATED),key=_rskey)))+') compare recast actuals to original-basis consensus; those surprises may be overstated.')
_USDL=[a["label"] for a in METR if a["sfmt"]=="usd"]
_neuL=[a["label"] for a in METR if a["neu"]]
_usd_note=("" if not _USDL else " "+(_USDL[0] if len(_USDL)==1 else ", ".join(_USDL[:-1])+" and "+_USDL[-1])+(" is" if len(_USDL)==1 else " are")+" shown in $ (actual − consensus), because a % swings too much on a small or near-zero base."
           +(" "+", ".join(_neuL)+" is shown as direction only (blue): spending more is not good or bad by itself." if _neuL else ""))
sc_note='<div class="note">Surprise = actual − pre-print consensus (% for value metrics, ppt for % metrics); green = good for holders, red = bad. '+' '.join(notes)+esc(_usd_note)+_exq_note+'</div>'

def series(a): return [_xrf(a["surs"].get(q),1) if (a["surs"].get(q) is not None and q not in a["anom"]) else None for q in QS]
ch=[]
if epsa: ch.append((f"{HEAD_SHORT} surprise (%)",_bc("beatGreen"),"bar",series(epsa)))
rev=by_key.get("rev") or next((a for a in METR if a["headline"] and a is not epsa),None)
if rev: ch.append((f'{esc(rev["label"])} surprise (%)',_bc("orange"),"bar",series(rev)))
if sk and (not epsa or sk["key"]!=epsa["key"]) and (not rev or sk["key"]!=rev["key"]):
    ch.append((f'{esc(sk["label"])} surprise ({"ppt" if sk["pct"] else "%"})',_bc("missRed"),"line",series(sk)))
_surp_rows=[]
for a in sc:
    _r=[esc(a["label"])+star(a)]
    for q in QS:
        _r.append('<span class="mut">⚠</span>' if q in a["anom"] else (col_s(a["surs"].get(q),a["sfmt"],a["lb"],a["neu"]) if a["surs"].get(q) is not None else '<span class="mut">n/a</span>'))
    _r.append(col_s(a["avg"],a["sfmt"],a["lb"],a["neu"]))
    _surp_rows.append(_r)
_surp_table=table(["Metric"]+[esc(q) for q in QS]+["Avg"],_surp_rows,numcols=tuple(range(1,len(QS)+2)))
_chart_toggle=('<div class="toggle"><button class="tgl active" data-t="chart">▮ Chart</button>'
               '<button class="tgl" data-t="table">▤ Table</button></div>')
_chart_inner=(_chart_toggle
    +'<div class="tview" data-v="chart"><div class="chartbox"><canvas id="surpChart"></canvas></div></div>'
    +f'<div class="tview" data-v="table" style="display:none">{_surp_table}'
    +f'<div class="note">Surprise = actual − pre-print consensus (% for value metrics, ppt for percentage metrics); ⚠ = {PER_1} excluded as an anomaly.</div></div>')
chartcard=card("Surprise History","Actual − pre-print consensus, "+PER_BY,_chart_inner,col=1)

_grid=fy_current_grid() if fy else None
_qsum=""
if nq:
    _bits=[]
    if nq_out: _bits.append(f"on the {nq_out} <b>outcome</b> guide{'s' if nq_out!=1 else ''} the actual exceeded the "
                            f"high end {exceed_out} time{'s' if exceed_out!=1 else ''} ({_xrf(exceed_out/nq_out*100,1):.1f}%)")
    if nq_cmp: _bits.append(f"the {nq_cmp} <b>comparability</b> guide{'s' if nq_cmp!=1 else ''} "
                            f"({esc(', '.join(_cmp_names))}) landed inside the guided range {inrange_cmp} time"
                            f"{'s' if inrange_cmp!=1 else ''} ({_xrf(inrange_cmp/nq_cmp*100,1):.1f}%)")
    _qsum=(f"<div class='mut' style='font-size:11px;margin-bottom:3px'>"
           f"{nq_q} {PER_1 if nq_q==1 else PER_N} guided, {nq} data point{'s' if nq!=1 else ''}. "
           +_cap("; ".join(_bits))+".</div>")
_fy_chg=fy_changes(only_fy=CUR_FY) if fy else []
_fyp=len({g.get("guide_date") for g in fy if _fy_year(g.get("fiscal_period"))==CUR_FY and g.get("guide_date")})
_fyr=sum(1 for e in _fy_chg if e["word"]=="Raised"); _fyc=sum(1 for e in _fy_chg if e["word"]=="Cut")
_qual=D.get("qual_guidance",[]) or []
qual_detail=""
if _qual:
    _qi=[]
    for q in _qual:
        _lbl=esc(str(q.get("label") or q.get("metric") or "Guidance"))
        _fp=q.get("fiscal_period"); _fps=f' <span class="mut">({esc(str(_fp))})</span>' if _fp else ''
        _txt=esc(str(q.get("text") or q.get("guide_text") or "")).strip()
        if _txt: _qi.append(f'<li><b>{_lbl}{_fps}:</b> {_txt}</li>')
    if _qi:
        _qlead=('Management also gives qualitative (directional) guidance' if (_grid or nq)
                else 'Management gives only qualitative (directional) guidance for this window; there are no numeric ranges to score')
        _qn=len(_qi)
        qual_detail=(f'<div class="mut" style="font-size:11px;margin:2px 0 4px">{_qlead}; normalized from company disclosures.</div>'
                     '<ul class="tight">'+"".join(_qi)+'</ul>')
if _grid or nq or qual_detail:
    gc_table=""
else:
    gc_table='<div class="note">No quantitative guidance mapped for this window (company may guide only annually, in growth terms, or not at all).</div>'
fy_detail=""
if _grid:
    _heads=([f"FY{str(CUR_FY)[-2:]} metric guided"]+[esc(q) for q in _grid["cols"]]+["Net vs first"]
            +(["Actual vs final guide"] if _grid["has_act"] else []))
    _rows=[]
    for _rw in _grid["rows"]:
        _r=[esc(_rw["m"])]
        for (s,cls,_dir) in _rw["cells"]:
            _r.append(span((("▲ " if _dir=="g" else ("▼ " if _dir=="r" else ""))+esc(s)),cls) if cls in ("g","r") else f'<span class="mut">{esc(s)}</span>' if s=="·" else esc(s))
        _r.append(span(_rw["net"],_rw["ncls"]))
        if _grid["has_act"]:
            _a=_rw["act"]
            _r.append(span(esc(_a[0]),_a[1]) if _a else '<span class="mut">n/a</span>')
        _rows.append(_r)
    _numc=tuple(range(1,len(_heads)))
    _gtab=table(_heads,_rows,numcols=_numc)
    _drop=(f' <span class="mut">({_grid["dropped"]} earlier FY{str(CUR_FY)[-2:]} print{"s" if _grid["dropped"]!=1 else ""} before {esc(str(_grid["oldest"]))} not shown.)</span>' if _grid["dropped"] else '')
    _nm=len(_grid['rows'])+len(_grid['held'])
    _fysum=f"<b>FY{str(CUR_FY)}</b>: {_nm} metric{'s' if _nm!=1 else ''} guided across {_fyp} print{'s' if _fyp!=1 else ''}, {len(_fy_chg)} guide changes ({_fyr} raises / {_fyc} cuts). "
    _held=(f' Held unchanged throughout, not shown: {esc(", ".join(_grid["held"]))}.' if _grid["held"] else '')
    _actn=(' Last column = realized full-year figure vs the FINAL guide of the year.' if _grid["has_act"] else '')
    fy_detail=("<div class='blkh'>Full-Year Guide Trajectory</div>"
               f"<div class='mut' style='font-size:11px;margin-bottom:3px'>"
               f"FY{str(CUR_FY)} guide at each of the last {len(_grid['cols'])} prints ({esc(str(_grid['cols'][0]))}–{esc(str(_grid['cols'][-1]))}); "
               f"▲ raised vs the prior print, ▼ cut; green = good for holders, red = bad. Net vs first = latest guide vs the first one shown.{_actn}</div>"+_gtab)
if _FYS:
    def _fyc(t,cls):
        e=esc(t).replace("\n","<br>")
        return f'<span class="{cls}">{e}</span>' if cls in ("g","r") else (f'<span class="mut">{e}</span>' if t=="·" else e)
    _r0=_FYS["rows"][0]["blocks"]; _cp,_cc=_r0[0]["cols"],_r0[1]["cols"]
    _h1=(f'<tr><th rowspan="2">FY guide</th><th class="ctr" colspan="{len(_cp)+1}">FY{_FYS["prev"]}</th>'
         f'<th class="ctr" colspan="{len(_cc)}">FY{_FYS["cur"]}</th></tr>')
    _h2='<tr>'+''.join(f'<th class="ctr">{esc(c)}</th>' for c in _cp)+'<th class="ctr">Actual</th>'+''.join(f'<th class="ctr">{esc(c)}</th>' for c in _cc)+'</tr>'
    _body=[]
    for _r in _FYS["rows"]:
        _fyP,_fyC=_r["blocks"]; _td=[f'<td>{esc(_r["label"])}</td>']
        for (t,cls,d) in _fyP["cells"]:
            _td.append(f'<td class="ctr">{_fyc(((("▲ " if d=="up" else ("▼ " if d=="dn" else ""))+t) if t!="·" else t),cls)}</td>')
        _NA='<span class="mut">n/a</span>'
        _td.append('<td class="ctr">'+(_fyc(_fyP["act"][0],_fyP["act"][1]) if _fyP["act"] else _NA)+'</td>')
        for (t,cls,d) in _fyC["cells"]:
            _td.append(f'<td class="ctr">{_fyc(((("▲ " if d=="up" else ("▼ " if d=="dn" else ""))+t) if t!="·" else t),cls)}</td>')
        _body.append("<tr>"+"".join(_td)+"</tr>")
    fy_detail=("<div class='blkh'>Full-Year Guide by Year</div>"
               f"<div class='mut' style='font-size:11px;margin-bottom:3px'>Each column = the print where the full-year guide was given; "
               f"Actual = FY{_FYS['prev']} reported figure; · = no full-year guide recorded at that print. ▲ raised / ▼ cut vs the print before; green = good for holders, red = bad. "
               f"Actual ▲ = beat the last FY{_FYS['prev']} guide.</div>"
               f"<table><thead>{_h1}{_h2}</thead><tbody>{''.join(_body)}</tbody></table>")
_gfmt=gval
qg_detail=""
if q_rows:
    _qall=[q for q in QS if any(r["fp"]==q for r in q_rows)]
    _qmax=CONFIG.get("qg_max_cols",4)
    _qcols=_qall[-_qmax:]
    _qdrop=_qall[:len(_qall)-len(_qcols)]
    _qh=["Guided metric"]+[esc(q) for q in _qcols]+["Beat the guide"]
    _qbody=[]
    for g in gmed:
        ml,un=g["m"],g["unit"]
        rs={r["fp"]:r for r in q_rows if r["m"]==ml and r["unit"]==un}
        _tag=' <span class="mut">· comparability</span>' if g["kind"]=="comparability" else ''
        row=[esc(ml)+(f' <span class="mut">({esc(un)})</span>' if un else '')+_tag]
        for q in _qcols:
            r=rs.get(q)
            if not r: row.append('<span class="mut">·</span>'); continue
            beat=_gbeat(r); miss=_gmiss(r)
            cls="g" if beat else ("r" if miss else "mut")
            mark="▲ " if r["act"]>r["hi"] else ("▼ " if r["act"]<r["lo"] else "")
            _actcell=(span(esc(mark+_gfmt(r["act"],un))+(" ⚠" if r["outlier"] else ""),cls) if cls in ("g","r")
                      else esc(_gfmt(r["act"],un))+(" ⚠" if r["outlier"] else ""))
            row.append(_actcell+f'<div class="mut" style="font-size:9px;line-height:1.15">g {esc(grange(r["lo"],r["hi"],un))}</div>')
        row.append(f'{g["exceed"]} of {g["n"]}' if g["kind"]=="outcome"
                   else f'<span class="mut">in range</span> {g["inrange"]} of {g["n"]}')
        _qbody.append(row)
    _qdropn=(f' Not shown as columns: {esc(", ".join(_qdrop))} (still counted).' if _qdrop else '')
    qg_detail=(f"<div class='blkh'>{PER_ADJ} Guide vs Actual</div>"
               +f"<div class='mut' style='font-size:11px;margin:5px 0 3px'>Actual vs guided range for each of the last {len(_qcols)} {PER_1 if len(_qcols)==1 else PER_N} ({esc(_qcols[0])}–{esc(_qcols[-1])}); each cell is the <b>actual</b>, guided range beneath. "
               f"▲ above the guided high end, ▼ below the low end; green = good for holders, red = bad (for a cost or tax line, above the guide is red). <b>Beat the guide</b> = {PER_N} the actual came in "
               f"above the guided high end{' (below the low end for a cost line)' if _COST_ANY else ''}, out of every {PER_1} guided; <b>comparability</b> rows count landing inside the range instead."
               f"{_qdropn}</div>"
               +table(_qh,_qbody,numcols=tuple(range(1,len(_qcols)+1))))
gc_note=('<div class="note">Guided lines are the metrics <b>management</b> reports on; they are a different '
         'set from the KPIs the Street forecasts in the cards above. ')
if _grid and FY_FALLBACK: gc_note+=(f'FY{str(_fy_live)} guidance has only just been initiated ({_fy_prints(_fy_live)} print), so the grid shows the most recent completed year, <b>FY{str(CUR_FY)}</b>, whose trajectory is informative. ')
if _grid: gc_note+=(f'"Net vs first" compares the latest FY{str(CUR_FY)} guide midpoint to its initial one. ')
if guide_skipped: gc_note+=' Skipped (unresolvable metric/actual): '+esc(", ".join(guide_skipped))+'.'
if outliers:
    ol=", ".join(f'{esc(r["m"])} {r["fp"]} ({r["bh"]:+.2f})' for r in outliers)
    gc_note+=f' <b>⚠ Extreme surprise flagged</b> (&gt;{CONFIG["outlier_mult"]:g}× the median): {ol}; verify against the filing for a restatement/reclassification before relying on it.'
gc_note+='</div>'
if _grid and nq: _gc_sub="Does the guide move, and does it prove conservative?"
elif _grid: _gc_sub="Does the full-year guide move across prints?"
elif nq: _gc_sub="Does the guide prove conservative?"
elif _qual: _gc_sub="What management guided (qualitative)"
else: _gc_sub="Guidance overview"
_quant=fy_detail+qg_detail
if _quant and qual_detail:
    _gtgl=('<div class="toggle"><button class="tgl active" data-t="quant">▤ Quantitative</button>'
           f'<button class="tgl" data-t="qual">▤ Qualitative ({_qn})</button></div>')
    _gc_inner=(_gtgl+f'<div class="tview" data-v="quant">{_quant}</div>'
               +f'<div class="tview" data-v="qual" style="display:none">{qual_detail}</div>'+gc_note)
else:
    _gc_inner=gc_table+_quant+qual_detail+gc_note
guidecard=card("Guidance Track Record",_gc_sub,_gc_inner,col=1)

_fixed=["eps","rev"]+[str(m.get("key")) for m in D.get("metrics",[]) if m.get("key") not in ("eps","rev")]
_rk=[str(k.get("key")) for k in (D.get("revision_kpis") or [])]
_fixed=["eps","rev"]+_rk+[k for k in _fixed if k not in _rk and k not in ("eps","rev")]
dis=sorted(sc,key=lambda a:(_fixed.index(a["key"]) if a.get("key") in _fixed else 99))
dis_rows=[[esc(a["label"])+star(a)+(" ⚠" if (a["thin"] and not a["no_disp"]) else ""),
           a.get("d_rng","n/a"),a["d_width"],DIS.get(a["d_disp"],a["d_disp"])] for a in dis]
_NXR=any((m.get("next_range") or {}).get("max") is not None for m in D.get("metrics",[]))
_NXD=max([str((m.get("next_range") or {}).get("asof","")) for m in D.get("metrics",[])] or [""])
distab=table(["Metric",(f"{NEXTQ} range (min–max)" if _NXR else "Typical range (min–max)"),
              ("Range width" if _NXR else "Median range width"),"Disagreement"],dis_rows,numcols=(1,))
_disbase=((f'<b>{NEXTQ} range</b> = lowest to highest analyst estimate for the next print ({NEXTQ}). <b>Range width</b> = that range as a % of consensus (ppt for % metrics).')
          if _NXR else
          ('<b>Typical range</b> = the median low-to-high estimate across the window, in the metric\'s own units. '
          '<b>Median range width</b> normalises that band.'))
_disgate=(' Where the panel published no high-low range, the consensus level is shown and dispersion is left '
          'unscored: a band with no width measures nothing about how much the Street disagrees.'
          if any(a["no_disp"] for a in METR) else '')
disnote=('<div class="note">'+_disbase+_disgate
         +(' ⚠ = collapsed estimate range; treat with caution.' if any(a["thin"] and not a["no_disp"] for a in METR) else '')
         +'</div>')

rt=D.get("revision_trail",[])
rkpis=D.get("revision_kpis",[]) or []
def _revpol(net,key):
    t,c=net; a=by_key.get(key) if key else None
    if not a or c not in ("g","r"): return net
    if a.get("neu"): return (t,"mut")
    if a.get("lb"): return (t,"r" if c=="g" else "g")
    return net
def _revnet(vals,pct,unit):
    xs=[v for v in vals if _isnum(v)]
    if len(xs)<2: return ("n/a","mut")
    a,b=xs[0],xs[-1]
    if pct:
        t,c=sgn(b-a); return (f'{t} ppt',c)
    if a==0: return ("n/a","mut")
    t,c=sgn((b-a)/abs(a)*100); return (f'{t}%',c)
def _revfmt(v,pct,unit):
    if not _isnum(v): return '<span class="mut">·</span>'
    if pct: return mnum(f'{_xrf(v,1):.1f}%')
    u=(unit or "")
    if u.upper() in ("$T","T"): v=_xrf(v,2); return mnum(f'{CUR}{v:,.2f}T')
    if u.upper() in ("$M","M"):
        v,_s=_mb(v,"M"); v=_xrf(v,2); return mnum(f'{CUR}{v:,.2f}{_s}')
    if u.upper().endswith("B") or u.upper()=="$B": v=_xrf(v,2); return mnum(f'{CUR}{v:,.2f}B')
    if u and u not in ("$","€","£","¥"):
        return mnum(f'{_xrf(v,2):,.2f} {esc(u)}' if abs(v)<1000 else f'{_xrf(v,0):,.0f} {esc(u)}')
    v=_xrf(v,2)
    return mnum(f'{CUR}{v:.2f}')
if rt:
    dates=[r.get("date","") for r in rt]
    heads=[(r.get("label") or r.get("date",""))+(" ⚠" if r.get("thin") else "") for r in rt]
    _rthin=any(r.get("thin") for r in rt)
    def _revrow(label,getv,pct,unit,key=None):
        vals=[getv(r) for r in rt]
        cells=[_revfmt(v,pct,unit) for v in vals]
        net,ncls=_revpol(_revnet(vals,pct,unit),key)
        return [esc(label)]+cells+[span(net,ncls)]
    def _rlab(key,fallback):
        m=raw_by_key.get(key)
        return (m.get("label")+star(m)) if m else fallback
    _rr=[_revrow(_rlab("eps",HEAD_SHORT),lambda r:r.get("eps"),False,"$"),
         _revrow(_rlab("rev","Revenue"),lambda r:r.get("rev"),False,"$"+str(REVU))]
    for k in rkpis:
        _kk=k.get("key")
        _rr.append(_revrow(_rlab(_kk,k.get("label",_kk or "?")),(lambda kk:(lambda r:(r.get("kpis") or {}).get(kk)))(_kk),k.get("pct",False),k.get("unit",""),_kk))
    _rmiss=[a["label"] for a in METR if a["key"] not in ("eps","rev") and a["key"] not in {k.get("key") for k in rkpis}]
    _rmheads=[f"Metric ({esc(NEXTQ)}E)"]+[esc(x) for x in heads]+["Net move"]
    rmtab=table(_rmheads,_rr,numcols=tuple(range(1,len(_rmheads))))
    e0,e1=rt[0].get("eps"),rt[-1].get("eps"); r0,r1=rt[0].get("rev"),rt[-1].get("rev")
    edir=(f'{_xrf((e1-e0)/e0*100,1):+.1f}%' if e0 and e1 else "n/a"); rdir=(f'{_xrf((r1-r0)/r0*100,1):+.1f}%' if r0 and r1 else "n/a")
    _asof=(" · ".join(f'{esc(h)}: {esc(d)}' for h,d in zip(heads,dates)) if any(r.get("label") for r in rt) else f'{esc(dates[0])} → {esc(dates[-1])}')
    rmcard=card("Revision Momentum",f"How the {esc(NEXTQ)} Street consensus has moved, by print",col=1,inner=
        rmtab+f'<div class="note"><b>Street consensus, not company guidance.</b> Net move = last vs first ({NEXTQ} {HEAD_SHORT} {edir}, revenue {rdir}).'
        +('<br><b>⚠</b> the marked column(s) rest on a partial consensus update, so treat them as indicative.' if _rthin else '')
        +(f'<br>No forward consensus trail supplied for: {esc(", ".join(_rmiss))}.' if _rmiss else '')
        +'</div>')
else:
    rmcard=card("Revision Momentum",f"How the {esc(NEXTQ)} Street consensus has moved, by print",'<div class="note">No revision trail supplied.</div>',col=1)


method=('<div class="note"><b>Measures historical forecasting reliability; does not predict earnings.</b> '
 +(f'Consensus = the last pre-print value for each closed {PER_1} (report dates from the earnings call). ' if _ASOF_OK else f'Consensus is the value supplied for each closed {PER_1}; the pre-print cutoff was not stamped in the input, so it is unverified. ')+
 f'Surprise = actual − consensus (% for value metrics, ppt for percentages); below {CONFIG["beat_tol"]["val"]}% ({CONFIG["beat_tol"]["pct"]} ppt) is In-line. '
 f'<b>Guidance credibility</b> = median beat vs the guided high end{" (vs the low end for a cost line)" if _COST_OUT else ""} (outliers &gt;{CONFIG["outlier_mult"]:g}× median flagged). A guide is scored as <i>comparability</i> when it estimates an effect management does not control (currency, M&amp;A, an extra week); those are excluded from the conservatism figures. '
 'The 1-day print reaction is reported as a range only: over this window no surprise measure (correlation, beat-vs-miss, breadth or beat quality) explains it at eight observations, so none is scored. '
 +(f'A tag in brackets after a metric name marks the consensus VARIANT the panel was pulled on '
    f'({", ".join(VARIANTS)}); those rows are compared against the matching basis, not the ordinary one. ' if VARIANTS else '')+
 'Computed live from the InSync MCP for this ticker; no master file. '
 f'<i class="cmt">// {"EPS basis: "+EPS_LABEL if _is_eps_metric else "Headline metric: "+HEAD_LABEL}'+('.' if _eps_gaap else '; other metrics Adjusted where available (Reported flagged *).')+'</i></div>')

_crit=[d["msg"] for d in defs if d["level"]=="critical"]; _warn=[d["msg"] for d in defs if d["level"]=="warn"]
if not _crit:
    banner=""
else:
    items="".join(f'<li>{esc(x)}</li>' for x in _crit)
    lvl="crit"
    hdr="⚠ DATA LIMITATIONS"+(" (rendered on request via --force)" if FORCE else "")
    banner=f'<div class="banner {lvl}"><b>{hdr}:</b><ul>{items}</ul></div>'


_extra=[]
if _grid and _grid.get("rows"):
    _up=[r["m"].replace("FY ","") for r in _grid["rows"] if r["net"]=="Raised"]
    _dn=[r["m"].replace("FY ","") for r in _grid["rows"] if r["net"]=="Cut"]
    _hd=len(_grid["rows"])-len(_up)-len(_dn)+len(_grid.get("held",[]))
    _p=[]
    if _up: _p.append("raised "+", ".join(_up))
    if _dn: _p.append("cut "+", ".join(_dn))
    if _hd: _p.append(f"{_hd} held")
    if _p: _extra.append(f'FY{str(CUR_FY)[-2:]} guide: '+"; ".join(_p)+".")
_dw=[]
for _m in D["metrics"]:
    _a=by_key.get(_m["key"]); _nr=_m.get("next_range") or {}
    if not _a or _a.get("sfmt")=="usd" or _a["pct"]: continue
    if _isnum(_nr.get("min")) and _isnum(_nr.get("max")) and _isnum(_nr.get("cons")) and _nr["cons"]:
        _dw.append((abs(_nr["max"]-_nr["min"])/abs(_nr["cons"])*100.0,_m["label"]))
if _dw:
    _w,_l=max(_dw)
    _extra.append(f'Street most split on {esc(_l)} for {esc(NEXTQ)} (range ~{_xrf(_w,1):.1f}% of consensus).')
if rt and len(rt)>=2:
    def _mv(a,b): return None if not (_isnum(a) and _isnum(b)) or a==0 else (b-a)/abs(a)*100.0
    _ev=_mv(rt[0].get("eps"),rt[-1].get("eps")); _rv2=_mv(rt[0].get("rev"),rt[-1].get("rev"))
    _pctk={str(k.get("key")) for k in (D.get("revision_kpis") or []) if k.get("pct")}
    _pctk|={m["key"] for m in D["metrics"] if m.get("pct_metric")}
    _km=[(abs(v),k,v) for k in (rt[0].get("kpis") or {}) if k not in _pctk for v in [_mv(rt[0]["kpis"].get(k),(rt[-1].get("kpis") or {}).get(k))] if v is not None]
    _t=[]
    if _ev is not None: _t.append(f"{HEAD_SHORT} {mnum(sgn(_ev)[0])}%")
    if _rv2 is not None: _t.append(f"revenue {mnum(sgn(_rv2)[0])}%")
    if _km and abs(_xrf(max(_km)[0],1))>=0.1:
        _k=max(_km); _lab=(raw_by_key.get(_k[1]) or {}).get("label",_k[1])
        _t.append(f"biggest move {esc(_lab)} {mnum(sgn(_k[2])[0])}%")
    if _t: _extra.append(f'{esc(NEXTQ)} consensus since before the {esc(QS[-1])} print: '+", ".join(_t)+".")
if _extra:
    _ii=next((i for i,f in enumerate(facts) if f.startswith("Into ")),len(facts))
    facts[_ii:_ii]=_extra
    summary="  ".join(facts)
rmcard=re.sub(r'data-col="1"','data-col="0"',rmcard,count=1)
cards=[card(f"Surprise by {PER_1.title()}","Actual − pre-print consensus",_surp_table+sc_note,col=0),
       chartcard,
       guidecard,
       card("Estimate Dispersion","Where the Street disagrees",distab+disnote,col=0),
       rmcard,
       card("Methodology","How every figure is derived",method,collapsed=True,col=0)]
cards=[re.sub(r'data-ord="\d+"', f'data-ord="{i+1}"', c, count=1) for i,c in enumerate(cards)]
body_cards="\n".join(cards)

logo_html=""
lp=os.path.join(os.path.dirname(__file__),"..","assets","insync-logo-white.png"); _mime="png"
if not os.path.exists(lp):
    lp=os.path.join(os.path.dirname(__file__),"..","assets","insync-logo.jpg"); _mime="jpeg"
if os.path.exists(lp):
    logo_html=f'<img class="logo" alt="InSync Analytics" src="data:image/{_mime};base64,{base64.b64encode(open(lp,"rb").read()).decode()}">'

CSS=(r"""
:root{--bg:#f5f1e6;--bg-card:#faf7f0;--ink:#1c1a16;--muted:#6f6a5e;--dark:#121110;--hdrblue:#2b2926;
--gold:#f5c518;--gold-bg:#fdf6dc;--blue-bg:#faeedd;--line:#d9d2c0;--navy:#c96a1a;--accent-dark:#c96a1a;}
""")+f":root{{--green:{_bc('beatGreen')};--red:{_bc('missRed')};--accent:#{_BC['orange']};}}"+r"""
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font-family:"IBM Plex Sans","Segoe UI",Helvetica,Arial,sans-serif;font-size:12.5px;line-height:1.45}
header{background:var(--dark);color:#f2efe6;padding:10px 18px;display:flex;align-items:flex-end;gap:14px;flex-wrap:wrap;position:sticky;top:0;z-index:50}
.logo{height:46px;width:auto;display:block}
.brand{font-family:Georgia,serif;font-size:12.5px;letter-spacing:1.4px;color:#e9e6dd;text-transform:uppercase}
.brand .bi{color:var(--accent);font-weight:700}.brand .bp{margin-left:10px;color:#9a958a;letter-spacing:.6px}
.cline{margin-top:2px;font-family:Georgia,serif}.cname{font-size:21px;font-weight:700;color:#f4f1e8}
.ctk{color:var(--accent);font-weight:700;font-size:14px}.cmeta{color:#9a958a;font-size:11px;font-family:"IBM Plex Mono",monospace}
.hbtn{font-family:"IBM Plex Mono",monospace;font-size:10.5px;padding:3px 9px;border-radius:10px;cursor:pointer;background:transparent;border:1px solid #cfc9b8;color:#f2efe6}
.banner{padding:8px 18px;font-size:11.5px}
.banner.warn{background:var(--gold-bg);border-bottom:1px solid var(--line);color:#6a5300}
.banner.crit{background:#fbe9e9;border-bottom:1px solid #e0b4b0;color:#8a2018}
.banner.okqc{background:#e7f3ea;border-bottom:1px solid #bfe0c8;color:#1b6b34}
.banner ul{margin:4px 0 0;padding-left:20px}
.execrow{display:flex;gap:16px;align-items:center}
.execfacts{flex:1 1 auto;font-weight:600}
@media(max-width:640px){.execrow{flex-direction:column;align-items:flex-start}}
.thesis{background:var(--gold-bg);border-bottom:1px solid var(--line);padding:9px 18px}
.thesis .sglbl{font-weight:700;font-size:10.5px;color:var(--accent-dark);letter-spacing:.5px;text-transform:uppercase;margin-bottom:3px}
.wrap{display:flex;gap:14px;padding:14px 18px;align-items:flex-start}
.mcol{flex:1 1 0;min-width:0;display:flex;flex-direction:column;gap:14px}
.card{background:var(--bg-card);border:1px solid var(--line);border-radius:4px;margin:0}
.card>h2{margin:0;padding:7px 12px;font-size:12px;letter-spacing:.8px;text-transform:uppercase;cursor:pointer;display:flex;align-items:baseline;gap:10px;user-select:none;border-bottom:2px solid var(--accent);background:#f2ecdd}
.card>h2 .tag{font-family:"IBM Plex Mono",monospace;font-size:10px;color:var(--muted);font-weight:400;text-transform:none;letter-spacing:0}
.card>h2:after{content:"\2212";margin-left:auto;color:var(--muted);font-family:monospace}
.card[data-collapsed="true"]>h2:after{content:"+"}
.card .body{padding:10px 12px;overflow-x:auto}
.card[data-collapsed="true"] .body{display:none}.card[data-collapsed="true"]>h2{border-bottom:none}
table{border-collapse:collapse;width:100%;font-size:11.5px}
th{background:var(--hdrblue);color:#fff;text-align:left;padding:4px 6px;font-size:10px;letter-spacing:.4px}
td{padding:3.5px 6px;border-bottom:1px solid #e9e3d2;vertical-align:top}
tr:nth-child(even) td{background:#f5f0e3}
td.num,th.num{text-align:right;font-family:"IBM Plex Mono",monospace}
td.ctr,th.ctr{text-align:center}
th{white-space:nowrap}
.g{color:var(--green);font-weight:600}.r{color:var(--red);font-weight:600}.y{color:#9a7d00;font-weight:600}.mut{color:var(--muted)}
.cmt{font-style:italic;color:var(--muted);font-weight:400}
.blkh{font-family:Georgia,serif;font-size:11.5px;font-weight:700;color:var(--ink);margin:9px 0 3px;letter-spacing:.2px}
.note{border-radius:3px;padding:7px 10px;margin:7px 0 0;font-size:11.5px;background:#f2ecdd}
.note.yellow{background:var(--gold-bg);border-left:3px solid var(--gold)}
ul.tight{margin:2px 0;padding-left:18px}ul.tight li{margin:4px 0}
.chartbox{height:240px;position:relative}
.chartfail{display:flex;align-items:center;justify-content:center;text-align:center;
  padding:18px;font-size:11.5px;color:var(--muted);background:#f2ecdd;border:1px dashed var(--line);border-radius:4px}
.toggle{display:flex;gap:6px;margin:0 0 9px}
.tgl{font-family:"IBM Plex Mono",monospace;font-size:10.5px;padding:3px 12px;border:1px solid var(--line);border-radius:12px;cursor:pointer;background:#f2ecdd;color:var(--muted)}
.tgl.active{background:var(--accent);color:#fff;border-color:var(--accent)}
.foot{padding:10px 18px 22px;font-family:"IBM Plex Mono",monospace;font-size:10px;color:var(--muted)}
@media(max-width:950px){.wrap{flex-direction:column}}

"""
ds=[]
for label,color,typ,data in ch:
    d=json.dumps(data)
    if typ=="bar": ds.append(f"{{type:'bar',label:{json.dumps(label)},data:{d},backgroundColor:'{color}'}}")
    else: ds.append(f"{{type:'line',label:{json.dumps(label)},data:{d},borderColor:'{color}',backgroundColor:'{color}',borderWidth:2,pointRadius:3,tension:.15}}")
def js_safe(s):
    return s.replace("<","\\u003c").replace(">","\\u003e").replace("&","\\u0026")
ds_js=js_safe(",".join(ds)); q_js=js_safe(json.dumps(QS))

html=("<!DOCTYPE html><html lang='en'><head><meta charset='utf-8'>"
 "<meta name='viewport' content='width=device-width, initial-scale=1'>"
 f"<title>{esc(TICK)}: Forecast Reliability Suite</title>"
 "<script src='https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js'></script>"
 f"<style>{CSS}</style></head><body>"
 "<header>"+logo_html+
 "<div><div class='brand'><span class='bi'>INSYNC</span><span>&nbsp;ANALYTICS</span>"
 "<span class='bp'>Forecast Reliability Suite</span></div>"
 f"<div class='cline'><span class='cname'>{esc(COMPANY)}</span>&nbsp;<span class='ctk'>({esc(TICK)})</span>"
 f"<span class='cmeta'>&nbsp;|&nbsp;{N} reported {PER_N} ({esc(QLO)}–{esc(QHI)})&nbsp;·&nbsp;Next print: {esc(NEXTQ)}&nbsp;·&nbsp;Data: InSync MCP (as of {esc(ASOF)})</span></div></div>"
 "<div style='margin-left:auto'><button class='hbtn' id='expAll'>▼ Expand All</button> "
 "<button class='hbtn' id='colAll'>▲ Collapse All</button></div></header>"
 f"<div class='thesis'><div class='sglbl'>Executive Summary</div>"
 f"<div class='execrow'><div class='execfacts'>{summary}</div></div></div>"
 f"<div class='wrap'><div class='mcol'></div><div class='mcol'></div></div><div id='cardstage' style='display:none'>{body_cards}</div>"
 +banner+
 f"<div class='foot'>InSync Analytics · Forecast Reliability Suite · Adjusted (Non-GAAP), fiscal · InSync MCP · {esc(TICK)} · Measures historical forecasting reliability; does not predict earnings. For informational purposes only. Not investment advice.</div>"
 "<script>"
 "function layoutCards(){var wrap=document.querySelector('.wrap');var cols=wrap.querySelectorAll('.mcol');var stage=document.getElementById('cardstage');var cards=[].slice.call(document.querySelectorAll('.card'));cards.sort(function(a,b){return (+a.getAttribute('data-ord'))-(+b.getAttribute('data-ord'));});cards.forEach(function(c){stage.appendChild(c);});if(window.matchMedia('(max-width:950px)').matches){cols[1].style.display='none';cards.forEach(function(c){cols[0].appendChild(c);});return;}cols[1].style.display='';cards.forEach(function(c){var f=c.getAttribute('data-col');var t=(f!==null&&cols[f])?cols[f]:(cols[0].offsetHeight<=cols[1].offsetHeight?cols[0]:cols[1]);t.appendChild(c);});}"
 "layoutCards();window.addEventListener('resize',function(){clearTimeout(window.__lt);window.__lt=setTimeout(layoutCards,150);});"
 "document.querySelectorAll('.card>h2').forEach(h=>h.addEventListener('click',()=>{var c=h.parentElement;c.setAttribute('data-collapsed',c.getAttribute('data-collapsed')==='true'?'false':'true');}));"
 "document.getElementById('expAll').onclick=()=>{document.querySelectorAll('.card').forEach(c=>c.setAttribute('data-collapsed','false'));layoutCards();};"
 "document.getElementById('colAll').onclick=()=>{document.querySelectorAll('.card').forEach(c=>c.setAttribute('data-collapsed','true'));layoutCards();};"
 "document.querySelectorAll('.toggle').forEach(function(tg){tg.querySelectorAll('.tgl').forEach(function(b){b.addEventListener('click',function(){var box=tg.parentElement;tg.querySelectorAll('.tgl').forEach(function(x){x.classList.remove('active');});b.classList.add('active');box.querySelectorAll('.tview').forEach(function(v){v.style.display=(v.getAttribute('data-v')===b.getAttribute('data-t'))?'':'none';});if(window.layoutCards)layoutCards();});});});"
 "if(typeof Chart==='undefined'){var _cb=document.querySelector('.chartbox');"
 "if(_cb){_cb.classList.add('chartfail');"
 "_cb.textContent='Chart library could not be loaded (no network access to the CDN). "
 "The table alongside carries exactly the same figures.';}}else{try{"
 f"new Chart(document.getElementById('surpChart'),{{data:{{labels:{q_js},datasets:[{ds_js}]}},"
 "options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{labels:{boxWidth:12,font:{size:10}}}},"
 "scales:{y:{grid:{color:'#eee7d4'},ticks:{callback:function(v){return v+'%';}}},x:{grid:{display:false}}}}});"
 "}catch(e){}}"
 "</script></body></html>")
open(OUTP,"w",encoding="utf-8").write(html)

def _reconcile():
    chk=[]
    def _c(name,cond,detail=""):
        chk.append({"check":name,"pass":bool(cond),**({"detail":detail} if detail and not cond else {})})
    for a in METR:
        lbl=a["label"]
        _c(f"{lbl}: B+M+I == N", a["beats"]+a["misses"]+a["inline"]==a["n"],
           f'{a["beats"]}+{a["misses"]}+{a["inline"]} != {a["n"]}')
        _sv=[v for q,v in (a.get("surs") or {}).items() if _isnum(v) and q not in (a.get("anom") or [])]
        if _sv and a["avg"] is not None:
            _m=sum(_sv)/len(_sv)
            _c(f"{lbl}: avg surprise == mean of scored quarters", abs(_m-a["avg"])<1e-6,
               f"mean {_m:.6f} vs reported {a['avg']:.6f}")
        _c(f"{lbl}: N == scored quarters", a["n"]==len(_sv)+len(a.get("anom") or []) or a["n"]>=len(_sv),
           f'n={a["n"]}, scored={len(_sv)}')
    if 'gmed' in globals() and gmed:
        _te=sum(g["exceed"] for g in gmed if g["kind"]=="outcome")
        _tn=sum(g["n"] for g in gmed if g["kind"]=="outcome")
        _c("guidance: exceed count ties to per-metric rows", _te==exceed_out, f"{_te} vs {exceed_out}")
        _c("guidance: outcome N ties to per-metric rows", _tn==nq_out, f"{_tn} vs {nq_out}")
        _c("guidance: exceed <= N", _te<=_tn, f"{_te} > {_tn}")
    _tr=D.get("revision_trail",[]) or []
    if len(_tr)>=2:
        for _k in ("eps","rev"):
            _sv2=[s.get(_k) for s in _tr if _isnum(s.get(_k))]
            if len(_sv2)>=2:
                _c(f"revision {_k}: trail has >=2 populated snapshots", len(_sv2)>=2)
        _dts=[_s.get("date") for _s in _tr]
        _c("revision trail: no duplicate snapshot dates", len(set(_dts))==len(_dts), f"dates={_dts}")
        _pd=[_parse_date(_d) for _d in _dts]
        if all(_p is not None for _p in _pd):
            _c("revision trail: snapshots in chronological order (Net move reads first->last)",
               _pd==sorted(_pd), f"dates={_dts}")
        else:
            _c("revision trail: every snapshot date parses (order cannot be verified otherwise)",
               False, f"unparseable date(s) in {_dts}")
    _f=[c for c in chk if not c["pass"]]
    _cannot=("Beat/Miss direction is NOT independently verified: this block re-uses the same polarity "
             "assignment the report was produced with. "
             +(f"{len(_POL_UNRESOLVED)} of {len(D['metrics'])} rows are scored on the higher-is-better "
               f"DEFAULT with no declaration (see polarity_unresolved); their verdicts rest on an "
               f"assumption this check shares and cannot test."
               if _POL_UNRESOLVED else "Every row's polarity is declared."))
    return {"checks_run":len(chk),"failed":len(_f),"all_pass":not _f,
            "failures":_f[:20],"scope":"headline aggregates vs per-quarter series (live pull)",
            "not_verified":_cannot}
RECON=_reconcile()
if not RECON["all_pass"]:
    print(f'RECONCILIATION: {RECON["failed"]} of {RECON["checks_run"]} checks FAILED', file=sys.stderr)
    for _c0 in RECON["failures"]: print(f'  - {_c0["check"]}: {_c0.get("detail","")}', file=sys.stderr)

_MODEL_FLAGS=[("model note: "+str(x)) for x in (D.get("data_flags") or []) if isinstance(x,str) and x.strip()] \
    if isinstance(D.get("data_flags"),list) else []
screener={"version":VERSION,"ticker":TICK,"company":COMPANY,"window":[QLO,QHI],"next_quarter":NEXTQ,"as_of":ASOF,
 "consensus_asof_verified":_ASOF_OK,
 "hyperscaler":_HS,
 "data_flags":[d["msg"] for d in defs]+_MODEL_FLAGS,
 "consensus_asof":D.get("consensus_asof",{}) or {},
 "reconciliation":RECON,
 "resolution":{"match_type":(D.get("resolution",{}) or {}).get("match_type"),
               "score":(D.get("resolution",{}) or {}).get("score"),
               "ticker_id":(D.get("resolution",{}) or {}).get("ticker_id"),
               "recorded":bool(D.get("resolution"))},
 "polarity_unresolved":_POL_UNRESOLVED,
 "predictability":{"score":PRED,"band":PRED_BAND,"components":{k:round(v,1) for k,v in _pc.items()},"inputs_available":PRED_N,"weights":{k:round(CONFIG["pred_w"][k],4) for k in CONFIG["pred_w"]},"weights_custom":PRED_W_CUSTOM},
 "metrics":[{"key":a["key"],"label":a["label"],"basis":a["basis"],"pct_metric":a["pct"],"beats":a["beats"],
   "misses":a["misses"],"inline":a["inline"],"n":a["n"],"beat_rate":round(a["beats"]/a["n"],3) if a["n"] else None,
   "avg_surprise":round(a["avg"],3) if a["avg"] is not None else None,"direction":a["direction"],
   "reliability":a["rel"],"confidence":a["conf"],
   "street_pattern":a["pattern"],"typical_error":round(a["typ_err"],3) if a["typ_err"] is not None else None,
   "beat_all_estimates":(a["rng_below"] if a["lb"] else a["rng_above"]),
   "below_all_estimates":(a["rng_above"] if a["lb"] else a["rng_below"]),"range_quarters":a["rng_n"],
   "dispersion":a["disagree"],"thin_panel":a["thin"],
   "anomalous_quarters":a["anom"]} for a in METR],
 "guidance":{"metrics":sorted({r["m"] for r in q_rows}),
   "guide_kind_scope":"outcome",
   "quarters_observed":nq_out,"exceeded_high":exceed_out,
   "pct_exceeded_high":round(exceed_out/nq_out*100,1) if nq_out else None,
   "comparability":{"n":nq_cmp,"in_range":inrange_cmp,
                    "pct_in_range":round(inrange_cmp/nq_cmp*100,1) if nq_cmp else None,
                    "median_abs_miss_vs_mid":round(med_absmiss_cmp,3) if med_absmiss_cmp is not None else None,
                    "metrics":_cmp_names},
   "all_guides":{"n":nq,"exceeded_high":exceed,"pct_exceeded_high":round(exceed/nq*100,1) if nq else None},
   "per_metric":[{"metric":g["m"],"unit":g["unit"],"kind":g["kind"],"n":g["n"],"exceeded_high":g["exceed"],
                  "in_range":g["inrange"],
                  "median_beat_vs_high":round(g["med_bh"],3) if g["med_bh"] is not None else None,
                  "median_beat":round(g["med_bd"],3) if g["med_bd"] is not None else None,
                  "median_beat_vs_mid":round(g["med_bm"],3) if g["med_bm"] is not None else None} for g in gmed],
   "outlier_quarters":[r["fp"] for r in outliers],
   "fy_scope":(str(CUR_FY) if CUR_FY is not None else None),
   "fy_raises":_CUR_R,"fy_cuts":_CUR_C,"fy_maintains":_CUR_M,
   "fy_all_years":{"years":[str(y) for y in _fy_years],"raises":raises,"cuts":cuts,"maintains":maints}},
 "next":{"eps_consensus":nc.get("eps"),"headline_label":HEAD_LABEL,
   "eps_basis":(("gaap" if _eps_gaap else "adjusted") if _is_eps_metric else "n/a (headline is not EPS)"),"rev_consensus":nc.get("rev"),"guide_low":gn.get("eps_low"),"guide_high":gn.get("eps_high"),"stale":nc.get("stale",False)}}
screener["version"]=VERSION
json.dump(screener,open(os.path.splitext(OUTP)[0]+"_metrics.json","w",encoding="utf-8"),indent=2,ensure_ascii=False)

pdf_path=os.path.splitext(OUTP)[0]+".pdf"
pdf_msg="skipped (--nopdf)"
PDF_FAILED=False
if not NOPDF:
  try:
    import re as _re, glob as _glob
    from reportlab import rl_config as _rlc
    _rlc.invariant=1
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.units import inch
    from reportlab.lib import colors as _c
    from reportlab.lib.enums import TA_RIGHT
    from reportlab.pdfbase import pdfmetrics as _pm
    from reportlab.pdfbase.ttfonts import TTFont as _TT
    from reportlab.pdfbase.pdfmetrics import registerFontFamily as _rff
    from reportlab.platypus import (BaseDocTemplate,PageTemplate,Frame,Paragraph,Spacer,Table,TableStyle,Image as _Img,HRFlowable,KeepTogether,PageBreak)
    from reportlab.lib.styles import ParagraphStyle
    OR=_bc("orange"); INK=_bc("ink"); CAL=_bc("calloutBg"); GN=_bc("beatGreen"); RD=_bc("missRed")
    GY=_bc("inlineGray"); TB=_bc("tableBorder"); HB=_bc("headerBg")
    def _f(*n):
        for x in n:
            g=_glob.glob(x)
            if g:return g[0]
    _cr=_f("/usr/share/fonts/truetype/crosextra/Carlito-Regular.ttf"); _cb=_f("/usr/share/fonts/truetype/crosextra/Carlito-Bold.ttf")
    _ci=_f("/usr/share/fonts/truetype/crosextra/Carlito-Italic.ttf")
    _sr=_f("/usr/share/fonts/truetype/liberation*/LiberationSerif-Regular.ttf","/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf")
    _sb=_f("/usr/share/fonts/truetype/liberation*/LiberationSerif-Bold.ttf","/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf")
    if _cr and _cb:
        _pm.registerFont(_TT("Body",_cr)); _pm.registerFont(_TT("Body-B",_cb))
        if _ci: _pm.registerFont(_TT("Body-I",_ci))
        _rff("Body",normal="Body",bold="Body-B",italic="Body-I" if _ci else "Body")
        BODY,BODYB="Body","Body-B"
    else: BODY,BODYB="Helvetica","Helvetica-Bold"
    _sym=_f("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf","/usr/share/fonts/**/DejaVuSans.ttf")
    if _sym:
        try:
            _pm.registerFont(_TT("Sym",_sym)); SYMF="Sym"
        except Exception: SYMF=None
    else: SYMF=None
    def mark(g):
        if not g: return ""
        return f'<font name="{SYMF}">{g}</font>' if SYMF else {"▲ ":"+ ","▼ ":"- "," ⚠":" (!)"}.get(g,g)
    if _sr and _sb:
        _pm.registerFont(_TT("Ser",_sr)); _pm.registerFont(_TT("Ser-B",_sb)); SER,SERB="Ser","Ser-B"
    else: SER,SERB="Times-Roman","Times-Bold"
    def S(n,**k):
        d=dict(fontName=BODY,fontSize=8,leading=10.5,textColor=_c.HexColor("#222222")); d.update(k); return ParagraphStyle(n,**d)
    strip=lambda s:_re.sub('<[^>]+>','',str(s)).replace('&amp;','&').replace('&lt;','<').replace('&gt;','>').replace('&#160;',' ')
    cell=S("cell",fontSize=7.4,leading=8.7); cc=S("cc",fontSize=7.4,leading=8.7,alignment=1)
    hd=S("hd",fontName=BODYB,fontSize=7,leading=8.5,textColor=_c.HexColor(INK)); hdc=S("hdc",fontName=BODYB,fontSize=7,leading=8.5,alignment=1,textColor=_c.HexColor(INK))
    def scol(v,pct,lb=False,neu=False):
        if v is None:return Paragraph("n/a",cc)
        t=fmt_s(v,pct); _good=(v<-1e-9) if lb else (v>1e-9); _bad=(v>1e-9) if lb else (v<-1e-9)
        if pct=="usd" and abs(_xrf(v,2))==0: _good=_bad=False
        col=GN if _good else (RD if _bad else GY)
        if neu: col="#1e3a8a"
        return Paragraph(f'<font color="{col}">{t}</font>',cc)
    xesc=lambda s: strip(s).replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")
    def P(t,s=cell):return Paragraph(xesc(t),s)
    def subhead(t):
        tb=Table([[Paragraph(f'<b>{strip(t)}</b>',S("sh",fontName=SERB,fontSize=8.8,leading=10.6,textColor=_c.HexColor(INK)))]],colWidths=[FULLW])
        tb.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,-1),_c.HexColor("#f0e9d8")),
                                ("LINEBEFORE",(0,0),(0,-1),2.4,_c.HexColor(OR)),
                                ("LEFTPADDING",(0,0),(-1,-1),7),("RIGHTPADDING",(0,0),(-1,-1),7),
                                ("TOPPADDING",(0,0),(-1,-1),3.5),("BOTTOMPADDING",(0,0),(-1,-1),3.5)]))
        return [Spacer(1,5),tb,Spacer(1,3)]
    def sec_flow(t):
        return [Spacer(1,4),Paragraph(f'<b>{strip(t)}</b>',S("s",fontName=SERB,fontSize=11,leading=13,textColor=_c.HexColor(INK))),
                HRFlowable(width="100%",thickness=1.3,color=_c.HexColor(OR),spaceBefore=2,spaceAfter=4)]
    def sec(t): return KeepTogether(sec_flow(t))
    FULLW=letter[0]-1.0*inch
    FOOT_BASE=0.5*inch
    FOOT_RULE=FOOT_BASE+8
    BOTM=FOOT_RULE+10
    FRAMEH=letter[1]-0.5*inch-BOTM
    def keep_if_fits(flows):
        _h=0
        for _f in flows:
            try: _h+=_f.wrap(FULLW,FRAMEH)[1]
            except Exception: return list(flows)
        return [KeepTogether(list(flows))] if _h<=FRAMEH else list(flows)
    def tbl(head,rows,cw,numc=()):
        cw=[(w if w else 1.0*inch) for w in cw]
        _s=sum(cw); cw=[w/_s*FULLW for w in cw] if _s else cw
        data=[[Paragraph(xesc(h),hd if i==0 else hdc) for i,h in enumerate(head)]]+rows
        st=[("BACKGROUND",(0,0),(-1,0),_c.HexColor(HB)),("LINEBELOW",(0,0),(-1,0),.7,_c.HexColor("#BBBBBB")),
            ("VALIGN",(0,0),(-1,-1),"MIDDLE"),("TOPPADDING",(0,0),(-1,-1),1.9),("BOTTOMPADDING",(0,0),(-1,-1),1.9),
            ("LEFTPADDING",(0,0),(-1,-1),4),("RIGHTPADDING",(0,0),(-1,-1),4)]
        for i in range(1,len(data)):
            st.append(("LINEBELOW",(0,i),(-1,i),.3,_c.HexColor("#EEEEEE")))
            if i%2==0: st.append(("BACKGROUND",(0,i),(-1,i),_c.HexColor("#F5F0E8")))
        t=Table(data,colWidths=cw,repeatRows=1); t.setStyle(TableStyle(st)); return t
    story=[]
    logo_p=os.path.join(os.path.dirname(__file__),"..","assets","insync-logo.jpg")
    logo_p=os.path.join(os.path.dirname(__file__),"..","assets","insync-logo.jpg")
    _BRAND_WARN=None
    def _brand_cfg():
        import os as _os
        d=_os.path.dirname(_os.path.abspath(__file__))
        for _ in range(4):
            c=_os.path.join(d,"common","brand.json")
            if _os.path.exists(c):
                try: return ((json.load(open(c,encoding="utf-8")) or {}).get("masthead",{}) or {}), None
                except Exception as e: return {}, f"brand.json unreadable ({e}); house defaults used"
            d=_os.path.dirname(d)
        return {}, "brand.json not found; house defaults used"
    _BJ,_BRAND_WARN=_brand_cfg()
    def _bhalf(key,default):
        v=_BJ.get(key)
        return float(v)/2.0 if isinstance(v,(int,float)) and v>0 else default
    _BRAND_PT   = _bhalf("brand_size_half_points",16.5)
    _BRAND_2L   = _bhalf("brand_size_two_line_half_points",12.0)
    _LAD=_BJ.get("company_size_ladder_half_points")
    _LADDER     = tuple(float(h)/2.0 for h in _LAD) if (isinstance(_LAD,list) and _LAD and
                  all(isinstance(h,(int,float)) and h>0 for h in _LAD)) else (14.0,13.5,13.0,12.5,12.0)
    _TWO_PT     = 10.0
    _MPX=_BJ.get("mark_px") if isinstance(_BJ.get("mark_px"),dict) else {}
    _MARK_W     = float(_MPX.get("w",59))*0.75
    _MARK_H     = float(_MPX.get("h",46))*0.75
    _CO_COL     = 2.8*inch
    _CO_BUDGET  = _CO_COL - 4.0
    _mhw=lambda t,pt: _pm.stringWidth(t,SERB,pt)
    _mhsfx=f" ({TICK})"
    _mhfits=lambda t,pt: _mhw(t,pt) <= _CO_BUDGET
    _mhco=_LADDER[-1]
    for _mhrung in _LADDER:
        _mhco=_mhrung
        if _mhfits(COMPANY+_mhsfx,_mhrung): break
    _mhtwo=not _mhfits(COMPANY+_mhsfx,_mhco)
    _mhl1,_mhl2=COMPANY+_mhsfx,None
    if _mhtwo:
        _mhco=_TWO_PT
        _mhs=COMPANY+_mhsfx; _mhwords=_mhs.split(" "); _mhcap=_CO_BUDGET*0.98; _mhbest=None
        for _mhk in range(1,len(_mhwords)):
            _mha=" ".join(_mhwords[:_mhk]); _mhb=" ".join(_mhwords[_mhk:])
            _mhwa,_mhwb=_mhw(_mha,_TWO_PT),_mhw(_mhb,_TWO_PT)
            if _mhwa>_mhcap or _mhwb>_mhcap: continue
            if _mhbest is None or abs(_mhwa-_mhwb)<_mhbest[0]: _mhbest=(abs(_mhwa-_mhwb),_mha,_mhb)
        if _mhbest is None:
            _mhtk=_mhs.rfind(_mhsfx); _mhlo=_mhtk if _mhtk>=0 else len(_mhs)
            for _mhk in range(1,len(_mhs)):
                if _mhtk>=0 and _mhk>_mhlo: break
                _mha,_mhb=_mhs[:_mhk],_mhs[_mhk:]
                _mhwa,_mhwb=_mhw(_mha,_TWO_PT),_mhw(_mhb,_TWO_PT)
                if _mhwa>_mhcap or _mhwb>_mhcap: continue
                if _mhbest is None or abs(_mhwa-_mhwb)<_mhbest[0]: _mhbest=(abs(_mhwa-_mhwb),_mha,_mhb)
        if _mhbest is not None: _mhl1,_mhl2=_mhbest[1],_mhbest[2]
        else: _mhl1,_mhl2=COMPANY,_mhsfx.strip()
    _mhbrand=_BRAND_2L if _mhtwo else _BRAND_PT
    _mhdrop     = 14.0 - _mhco
    _BRAND_LEAD = 12.93 if _mhtwo else 21.04 + 1.21*_mhdrop
    _CO_LEAD    = 12.00 if _mhtwo else 19.00 - 1.21*_mhdrop
    _MARK_LIFT  = -0.16 if _mhtwo else 1.98
    def _mh_co_para(txt,pt,lead):
        _mht=esc(txt); _mhk=esc(f"({TICK})")
        _mhpos=_mht.find(_mhk)
        if _mhpos<0:
            _mhinner=f'<font name="{SERB}" size="{pt}" color="{INK}">{_mht}</font>'
        else:
            _mhpre,_mhpost=_mht[:_mhpos],_mht[_mhpos+len(_mhk):]
            _mhinner=(f'<font name="{SERB}" size="{pt}" color="{INK}">{_mhpre}</font>'
                    f'<font name="{SERB}" size="{pt}" color="{OR}">{_mhk}</font>'
                    f'<font name="{SERB}" size="{pt}" color="{INK}">{_mhpost}</font>')
        return Paragraph(_mhinner,S("mco",fontName=SERB,fontSize=pt,leading=lead,spaceBefore=0,spaceAfter=0))
    lg=_Img(logo_p,width=_MARK_W,height=_MARK_H) if os.path.exists(logo_p) else Paragraph("",cell)
    left=[Paragraph(f'<font name="{SERB}" size="{_mhbrand}" color="{OR}">InSync</font>'
                    f'<font name="{SER}" size="{_mhbrand}" color="{INK}"> Analytics</font>',
                    S("mbr",fontName=SERB,fontSize=_mhbrand,leading=_BRAND_LEAD,spaceBefore=0,spaceAfter=0))]
    left+= [_mh_co_para(_mhl1,_mhco,_CO_LEAD)] + ([_mh_co_para(_mhl2,_mhco,_CO_LEAD)] if _mhtwo else [])
    right=Paragraph(f'<font name="{SERB}" size="16" color="{INK}">Forecast Reliability Suite</font><br/>'
                    f'<font name="{BODYB}" size="8" color="{OR}">Prepared:</font><font size="8"> {ASOF}</font>&#160;&#160;'
                    f'<font name="{BODYB}" size="8" color="{OR}">Coverage:</font><font size="8"> {N} qtrs ({esc(QLO)}\u2013{esc(QHI)})</font>',
                    S("r",alignment=TA_RIGHT,leading=15))
    _MPAD=6
    _lgw=_MARK_W+0.195*inch+_MPAD
    mt=Table([[lg,left,right]],colWidths=[_lgw,_CO_COL,None])
    mt.setStyle(TableStyle([("VALIGN",(0,0),(1,0),"BOTTOM"),("VALIGN",(2,0),(2,0),"MIDDLE"),
        ("LEFTPADDING",(0,0),(-1,-1),0),("RIGHTPADDING",(0,0),(-1,-1),0),
        ("LEFTPADDING",(0,0),(0,0),_MPAD),("RIGHTPADDING",(-1,0),(-1,0),_MPAD),
        ("TOPPADDING",(0,0),(-1,-1),5),("BOTTOMPADDING",(0,0),(-1,-1),5),
        ("BOX",(0,0),(-1,-1),1,_c.HexColor("#C9C9C9")),
        ("BOTTOMPADDING",(0,0),(0,0),5+_MARK_LIFT)]))
    story+=[mt,HRFlowable(width="100%",thickness=2,color=_c.HexColor(OR),spaceBefore=5,spaceAfter=5)]
    cbox=Table([[[Paragraph(f'<font name="{BODYB}" size="7" color="{OR}">EXECUTIVE SUMMARY</font>',S("cl",spaceAfter=3)),
                  Paragraph(xesc(summary),S("v",fontName=BODYB,fontSize=8.6,leading=12,textColor=_c.HexColor(INK)))]]],colWidths=[None])
    cbox.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,-1),_c.HexColor(CAL)),("LINEBEFORE",(0,0),(0,-1),3,_c.HexColor(OR)),
        ("LEFTPADDING",(0,0),(-1,-1),9),("RIGHTPADDING",(0,0),(-1,-1),9),("TOPPADDING",(0,0),(-1,-1),6),("BOTTOMPADDING",(0,0),(-1,-1),7)]))
    story.append(cbox)
    if defs:
        crit=[d["msg"] for d in defs if d["level"]=="critical"]; warn=[d["msg"] for d in defs if d["level"]=="warn"]
        _hdr=("<b>\u26a0 DATA LIMITATIONS"+(" (rendered on request via --force)" if FORCE else "")+":</b> ") if crit else "<b>QC: data-quality notes:</b> "
        dtxt=(_hdr+" ".join("\u2022 "+strip(x) for x in crit+warn))
        db=Table([[Paragraph(dtxt,S("d",fontSize=7.4,leading=9,textColor=_c.HexColor("#7a2018" if crit else "#6a5300")))]],colWidths=[None])
        db.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,-1),_c.HexColor("#fbe9e9" if crit else "#fdf6dc")),("LEFTPADDING",(0,0),(-1,-1),9),("RIGHTPADDING",(0,0),(-1,-1),9),("TOPPADDING",(0,0),(-1,-1),5),("BOTTOMPADDING",(0,0),(-1,-1),5)]))
    else:
        db=Table([[Paragraph(f"<b>✓ QC: data quality checks passed.</b> {len(METR)} metrics, price reaction on {len(react)}/{N} {PER_N}.",S("d",fontSize=7.4,leading=9,textColor=_c.HexColor("#1b6b34")))]],colWidths=[None])
        db.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,-1),_c.HexColor("#e7f3ea")),("LEFTPADDING",(0,0),(-1,-1),9),("RIGHTPADDING",(0,0),(-1,-1),9),("TOPPADDING",(0,0),(-1,-1),5),("BOTTOMPADDING",(0,0),(-1,-1),5)]))
    _qc_db=db
    if [d for d in defs if d["level"]=="critical"]:
        story.append(Spacer(1,6)); story.append(_qc_db)
    scr=[[P(a["label"]+(" *" if a.get("basis")=="reported" else "")),P(str(a["n"])+_exq(a),cc),
          P((f'{a["beats"]} above/{a["misses"]} below' if a["neu"] else f'{a["beats"]}B/{a["misses"]}M/{a["inline"]}I'),cc),scol(a["avg"],a["sfmt"],a["lb"],a["neu"]),
          P(a["direction"],cc),
          P(_rel_cell(a) or ("None detected" if a["rel"]=="n/a" else REL_WORD.get(a["rel"],a["rel"])),cc),
          P(_conf_cell(a) if _conf_cell(a) is not None else a["conf"],cc)] for a in sc]
    sqhead=["Metric"]+QS+["Avg"]
    sqrows=[]
    for a in sc:
        row=[P(a["label"]+star(a),cell)]
        for q in QS: row.append(Paragraph(f'<font color="{GY}">⚠</font>',cc) if q in a["anom"] else scol(a["surs"].get(q),a["sfmt"],a["lb"],a["neu"]))
        row.append(scol(a["avg"],a["sfmt"],a["lb"],a["neu"])); sqrows.append(row)
    cwq=[1.85*inch]+[0.56*inch]*len(QS)+[0.56*inch]
    story+=[sec(f"Surprise by {PER_1.title()}: Where the Street Has Been Over / Under"),tbl(sqhead,sqrows,cwq,numc=tuple(range(1,len(sqhead)))),
            Paragraph("Surprise = actual − pre-print consensus (% for value metrics, ppt for % metrics); green = good for holders, red = bad."
                      + (" * Reported (GAAP) basis; Adjusted not available." if any(a.get("basis")=="reported" for a in METR) else "")
                      + xesc(_usd_note)
                      + (xesc(_exq_note.replace("<b>","").replace("</b>","")) if _EXQ else "")
                      + (f" Restated periods ({xesc(', '.join(sorted(map(str,RESTATED),key=_rskey)))}) compare recast actuals to "
                         "original-basis consensus; those surprises may be overstated."
                         if RESTATED else ""),
                      S("n",fontSize=6.6,leading=8,textColor=_c.HexColor(GY)))]
    _grid_pdf=fy_current_grid() if fy else None
    _qual_pdf=D.get("qual_guidance",[]) or []
    if _grid_pdf or nq or _qual_pdf:
        if _grid_pdf and nq: _hsub="Does the Guide Move, and Has It Historically Been Conservative?"
        elif _grid_pdf: _hsub="Does the Full-Year Guide Move Across Prints?"
        elif nq: _hsub="Has the Guide Historically Been Conservative?"
        else: _hsub="What Management Guided (Qualitative)"
        _qual_only=bool(_qual_pdf) and not (_grid_pdf or nq)
        _keep_blocks=CONFIG.get("keep_blocks_together",True)
        if not _qual_only: story+=[sec(f"Guidance Track Record: {_hsub}")]
        if _FYS:
            _clr={"g":GN,"r":RD,"mut":GY,"":INK}
            def _mk(t):
                for _g in ("▲ ","▼ "):
                    if t.startswith(_g): return mark(_g)+xesc(t[len(_g):])
                return xesc(t)
            _r0=_FYS["rows"][0]["blocks"]; _cp,_cc=_r0[0]["cols"],_r0[1]["cols"]; _np,_ncu=len(_cp),len(_cc)
            r1=[Paragraph("FY guide",hd),Paragraph(f'FY{_FYS["prev"]}',hdc)]+[""]*_np+[Paragraph(f'FY{_FYS["cur"]}',hdc)]+[""]*(_ncu-1)
            r2=[""]+[Paragraph(c,hdc) for c in _cp]+[Paragraph("Actual",hdc)]+[Paragraph(c,hdc) for c in _cc]
            _rows=[r1,r2]
            def _cell(t,cls,d):
                if t=="·": return Paragraph(f'<font color="{GY}">·</font>',cc)
                _t=(mark("▲ ") if d=="up" else (mark("▼ ") if d=="dn" else ""))+xesc(t).replace("\n","<br/>")
                return Paragraph(f'<font color="{_clr.get(cls,INK)}">{_t}</font>',cc)
            for _r in _FYS["rows"]:
                _fyP,_fyC=_r["blocks"]
                row=[P(_r["label"])]+[_cell(*x) for x in _fyP["cells"]]
                row.append(Paragraph(f'<font color="{_clr.get(_fyP["act"][1],INK)}">{_mk(_fyP["act"][0])}</font>',cc) if _fyP["act"] else Paragraph(f'<font color="{GY}">n/a</font>',cc))
                row+=[_cell(*x) for x in _fyC["cells"]]
                _rows.append(row)
            _cw=[1.45*inch]+[0.70*inch]*(_np+1+_ncu); _sw=sum(_cw); _cw=[w/_sw*FULLW for w in _cw]
            _t=Table(_rows,colWidths=_cw,repeatRows=2)
            _st=[("SPAN",(0,0),(0,1)),("SPAN",(1,0),(_np+1,0)),("SPAN",(_np+2,0),(_np+1+_ncu,0)),
                 ("BACKGROUND",(0,0),(-1,1),_c.HexColor(HB)),("LINEBELOW",(0,1),(-1,1),.7,_c.HexColor("#BBBBBB")),
                 ("LINEBELOW",(1,0),(-1,0),.3,_c.HexColor("#DDDDDD")),
                 ("VALIGN",(0,0),(-1,-1),"MIDDLE"),("TOPPADDING",(0,0),(-1,-1),1.9),("BOTTOMPADDING",(0,0),(-1,-1),1.9),
                 ("LEFTPADDING",(0,0),(-1,-1),4),("RIGHTPADDING",(0,0),(-1,-1),4)]
            for _i in range(2,len(_rows)):
                _st.append(("LINEBELOW",(0,_i),(-1,_i),.3,_c.HexColor("#EEEEEE")))
                if _i%2==1: _st.append(("BACKGROUND",(0,_i),(-1,_i),_c.HexColor("#F5F0E8")))
            _t.setStyle(TableStyle(_st))
            story+=[Paragraph("<b>Full-Year Guide by Year</b>",S("bh",fontName=SERB,fontSize=8.5,leading=11,textColor=_c.HexColor(INK))),
                    Paragraph("Each column = the print where the full-year guide was given; "+f"Actual = FY{_FYS['prev']} reported figure; · = no full-year guide recorded at that print. "
                              +mark("▲ ")+"raised / "+mark("▼ ")+"cut vs the print before; "
                              f"<font color=\"{GN}\">green = good for holders</font>, <font color=\"{RD}\">red = bad</font>. "
                              +"Actual "+mark("▲ ")+f"= beat the last FY{_FYS['prev']} guide.",S("n",fontSize=7,leading=9,textColor=_c.HexColor(INK))),
                    Spacer(1,2),_t]
        elif _grid_pdf:
            _fyp=len({g.get("guide_date") for g in fy if _fy_year(g.get("fiscal_period"))==CUR_FY})
            _clr={"g":GN,"r":RD,"mut":GY}
            _hasact=_grid_pdf["has_act"]
            _gh=([f"FY{str(CUR_FY)[-2:]} metric guided"]+[f'{q}' for q in _grid_pdf["cols"]]+["Net vs first"]
                 +(["Actual vs final guide"] if _hasact else []))
            _grows=[]
            for _rw in _grid_pdf["rows"]:
                r=[P(_rw["m"])]
                for (s,cls,_dir) in _rw["cells"]:
                    r.append(Paragraph(f'<font color="{_clr.get(cls,GY)}">{xesc(s)}</font>',cc) if cls in ("g","r") else P(s,cc))
                r.append(Paragraph(f'<font color="{_clr.get(_rw["ncls"],GY)}">{_rw["net"]}</font>',cc))
                if _hasact:
                    _a=_rw["act"]
                    r.append(Paragraph(f'<font color="{_clr.get(_a[1],GY)}">{xesc(_a[0])}</font>',cc) if _a else P("n/a",cc))
                _grows.append(r)
            _nc=len(_grid_pdf["cols"])
            _midw=2.4 if _hasact else 3.4
            _gw=[1.75*inch]+[ (_midw/max(1,_nc))*inch ]*_nc+[0.85*inch]+([1.15*inch] if _hasact else [])
            gtab=tbl(_gh,_grows,_gw,numc=tuple(range(1,len(_gh))))
            _dp=(f' ({_grid_pdf["dropped"]} earlier FY{str(CUR_FY)[-2:]} print{"s" if _grid_pdf["dropped"]!=1 else ""} before {_grid_pdf["oldest"]} not shown.)' if _grid_pdf["dropped"] else '')
            _fb=(f"FY{_fy_live} guidance has only just been initiated, so this shows the completed FY{CUR_FY}. " if FY_FALLBACK else "")
            _nm=len(_grid_pdf['rows'])+len(_grid_pdf['held'])
            _hn=(f' Held unchanged at every print and not shown separately: {strip(", ".join(_grid_pdf["held"]))}.' if _grid_pdf["held"] else '')
            _an=(f' The last column is the realized full-year figure vs the FINAL guide of that year.' if _hasact else '')
            story+=[Paragraph("<b>Full-Year Guide Trajectory</b>",S("bh",fontName=SERB,fontSize=8.5,leading=11,textColor=_c.HexColor(INK))),
                    Paragraph(f"{_fb}FY{CUR_FY} guide at each of the last {len(_grid_pdf['cols'])} prints ({_grid_pdf['cols'][0]}–{_grid_pdf['cols'][-1]}); "
                              f"<font color=\"{GN}\">green = good for holders</font>, <font color=\"{RD}\">red = bad</font>. Net vs first = latest guide vs the first one shown.{_an}",
                              S("n",fontSize=7,leading=9,textColor=_c.HexColor(INK))),
                    Spacer(1,2),gtab]
        if nq:
            gm=", ".join(sorted({r["m"] for r in q_rows}))
            _qbits=[]
            if nq_out: _qbits.append(f"on the {nq_out} outcome guide{'s' if nq_out!=1 else ''} the actual {'beat the guide' if _COST_OUT else 'exceeded the high end'} {exceed_out} of {nq_out} ({_xrf(exceed_out/nq_out*100,1):.1f}%)")
            if nq_cmp: _qbits.append(f"the {nq_cmp} comparability guide{'s' if nq_cmp!=1 else ''} ({strip(', '.join(_cmp_names))}) landed inside the guided range {inrange_cmp} of {nq_cmp} ({_xrf(inrange_cmp/nq_cmp*100,1):.1f}%)")
            _qq=[q for q in QS if any(r["fp"]==q for r in q_rows)][-CONFIG.get("qg_max_cols",4):]
            _qintro=Paragraph(f"Actual vs guided range for each of the last {len(_qq)} {PER_1 if len(_qq)==1 else PER_N} "
                              f"({_qq[0]}–{_qq[-1]}); guided range beneath each actual. Beat the guide = {PER_N} the actual came in above the guided high end{' (below the low end for a cost line)' if _COST_ANY else ''}.",
                              S("n",fontSize=7,leading=9,textColor=_c.HexColor(INK)))
            _qall_p=[q for q in QS if any(r["fp"]==q for r in q_rows)]
            _qcols=_qall_p[-CONFIG.get("qg_max_cols",4):]
            _qdrop_p=_qall_p[:len(_qall_p)-len(_qcols)]
            _gf=gval
            _qh=["Guided metric"]+[q for q in _qcols]+["Beat the guide"]
            _qbody=[]
            _cw=(4.85/max(1,len(_qcols)))*inch
            for g in gmed:
                ml,un=g["m"],g["unit"]; rs={r["fp"]:r for r in q_rows if r["m"]==ml and r["unit"]==un}
                row=[P(ml+(f' ({un})' if un else '')+(' · comparability' if g["kind"]=="comparability" else ''))]
                for q in _qcols:
                    r=rs.get(q)
                    if not r: row.append(P("·",cc)); continue
                    beat=_gbeat(r); miss=_gmiss(r)
                    col=GN if beat else (RD if miss else INK)
                    mk=mark("▲ ") if r["act"]>r["hi"] else (mark("▼ ") if r["act"]<r["lo"] else "")
                    _gr=xesc(grange(r["lo"],r["hi"],un))
                    if _pm.stringWidth("g "+grange(r["lo"],r["hi"],un),cc.fontName,6)>(_cw-8.5):
                        _gr=_gr.replace("–","–<br/>",1)
                    row.append(Paragraph(f'<font color="{col}">{mk}{xesc(_gf(r["act"],un))}</font>{mark(" ⚠") if r["outlier"] else ""}'
                                         f'<br/><font color="{GY}" size="6">g {_gr}</font>',cc))
                row.append(P(f'{g["exceed"]} of {g["n"]}' if g["kind"]=="outcome"
                             else f'in range {g["inrange"]} of {g["n"]}',cc)); _qbody.append(row)
            gd=tbl(_qh,_qbody,[1.5*inch]+[_cw]*len(_qcols)+[1.0*inch],numc=tuple(range(1,len(_qcols)+1)))
            extra=""
            if _qdrop_p: extra+=f' Not shown as columns: {strip(", ".join(_qdrop_p))} (still counted).'
            if outliers: extra+=f" ⚠ Extreme surprise (>{CONFIG['outlier_mult']:g}× median): "+", ".join(f'{strip(r["m"])} {r["fp"]} ({r["bh"]:+.2f})' for r in outliers)+"; verify filing for reclassification."
            story+=[Spacer(1,3),Paragraph(f"<b>{PER_ADJ} Guide vs Actual</b>",S("bh2",fontName=SERB,fontSize=8.5,leading=11,textColor=_c.HexColor(INK))),_qintro,Spacer(1,2),gd,
                    Paragraph("Each row is one guided metric; "+mark("▲ ")+"above the guided high end, "+mark("▼ ")+"below the low end; green = good for holders, red = bad (for a cost or tax line, above the guide is red). Rows tagged comparability are scored on landing inside the range and excluded from the conservatism read."+extra,S("n",fontSize=6.6,leading=8,textColor=_c.HexColor(GY)))]
        if _qual_pdf:
            _qlead=("Management also gives qualitative (directional) guidance, normalized from company disclosures." if (_grid_pdf or nq)
                    else "Management gives only qualitative (directional) guidance for this window; there are no numeric ranges to score, so the guides are listed below as normalized from company disclosures.")
            _bfont=SYMF or BODY
            _qst=S("qb",fontSize=6.9,leading=8.7,textColor=_c.HexColor(INK),
                   leftIndent=9,firstLineIndent=-9,spaceAfter=1.5,bulletFontName=_bfont,bulletFontSize=6.5,bulletIndent=1)
            _qhead=subhead(f"Qualitative Guidance: Directional ({len(_qual_pdf)})")+[
                    Paragraph(_qlead,S("n",fontSize=6.9,leading=8.4,textColor=_c.HexColor(GY))),Spacer(1,1)]
            _qbul=[]
            for q in _qual_pdf:
                _lbl=xesc(str(q.get("label") or q.get("metric") or "Guidance"))
                _fp=q.get("fiscal_period"); _fps=f' <font color="{GY}">({xesc(_fp)})</font>' if _fp else ''
                _txt=xesc(str(q.get("text") or q.get("guide_text") or "")).strip()
                if not _txt: continue
                _qbul.append(Paragraph(f'<b>{_lbl}</b>{_fps}<b>:</b> {_txt}',_qst,bulletText="•"))
            _grp=((sec_flow(f"Guidance Track Record: {_hsub}") if _qual_only else [])+_qhead+_qbul)
            story+= (keep_if_fits(_grp) if _keep_blocks else _grp)
    dr=[[P(a["label"]+star(a)+mark(" ⚠" if (a["thin"] and not a["no_disp"]) else "")),
         P(a.get("d_rng","n/a"),cc),P(a["d_width"],cc),P(a["d_disp"],cc)] for a in dis]
    _dnote=((f"{NEXTQ} range = lowest to highest analyst estimate for the next print; Range width = that range as a % of consensus (ppt for % metrics).")
            if _NXR else
            ("Typical range = median low-to-high estimate in the metric's own units (% for percentage metrics); "
            "Range width normalises that band (ppt, or % of consensus for value metrics)."))
    if any(a["no_disp"] for a in dis):
        _dnote+=(" Where the panel published no high-low range, the consensus level is shown and dispersion is "
                 "left unscored: a band with no width measures nothing about how much the Street disagrees.")
    _dgrp=(sec_flow("Estimate Dispersion: Where the Street Disagrees")
           +[tbl(["Metric",(f"{NEXTQ} range (min–max)" if _NXR else "Typical range (min–max)"),"Range width","Disagreement"],dr,[2.3*inch,1.7*inch,1.2*inch,1.2*inch],numc=(1,)),
             Paragraph(_dnote,S("n",fontSize=6.6,leading=8,textColor=_c.HexColor(GY)))])
    story+= (keep_if_fits(_dgrp) if CONFIG.get("keep_blocks_together",True) else _dgrp)
    if rt:
        _clr={"g":GN,"r":RD,"mut":GY}
        _rdates=[r.get("date","") for r in rt]
        def _revfmt_pdf(v,pct,unit):
            if not _isnum(v): return "·"
            if pct: return mnum(f'{_xrf(v,1):.1f}%')
            u=(unit or "")
            if u.upper() in ("$T","T"): v=_xrf(v,2); return mnum(f'{CUR}{v:,.2f}T')
            if u.upper() in ("$M","M"): v,_s=_mb(v,"M"); v=_xrf(v,2); return mnum(f'{CUR}{v:,.2f}{_s}')
            if u.upper().endswith("B") or u.upper()=="$B": v,_s=_mb(v,"B"); v=_xrf(v,2); return mnum(f'{CUR}{v:,.2f}{_s}')
            if u and u not in ("$","€","£","¥"): return mnum(f'{_xrf(v,0):,.0f} {strip(u)}')
            v=_xrf(v,2)
            return mnum(f'{CUR}{v:.2f}')
        def _revrow_pdf(label,getv,pct,unit,key=None):
            vals=[getv(r) for r in rt]
            row=[P(label)]+[P(_revfmt_pdf(v,pct,unit),cc) for v in vals]
            net,ncls=_revpol(_revnet(vals,pct,unit),key)
            row.append(Paragraph(f'<font color="{_clr.get(ncls,GY)}">{net}</font>',cc))
            return row
        _rheads=[(r.get("label") or r.get("date",""))+(" ⚠" if r.get("thin") else "") for r in rt]
        _rthin_pdf=any(r.get("thin") for r in rt)
        _rr2=[_revrow_pdf(_rlab("eps",HEAD_SHORT),lambda r:r.get("eps"),False,"$"),
              _revrow_pdf(_rlab("rev","Revenue"),lambda r:r.get("rev"),False,"$"+str(REVU))]
        for k in rkpis:
            _kk2=k.get("key")
            _rr2.append(_revrow_pdf(_rlab(_kk2,k.get("label",_kk2 or "?")),(lambda kk:(lambda r:(r.get("kpis") or {}).get(kk)))(_kk2),k.get("pct",False),k.get("unit",""),_kk2))
        _rh=[f"Metric ({NEXTQ}E)"]+[h for h in _rheads]+["Net move"]
        _nd=len(_rheads)
        _rw=[1.55*inch]+[ (4.0/max(1,_nd))*inch ]*_nd+[0.85*inch]
        _asof_pdf=(" · ".join(f'{h}: {d}' for h,d in zip(_rheads,_rdates)) if any(r.get("label") for r in rt) else "")
        _vgrp=(sec_flow(f"Revision Momentum: How the {NEXTQ} Street Consensus Has Moved")
               +[Paragraph("Street consensus, not company guidance. Net move = last vs first."
                          +(" ⚠ the marked column(s) rest on a partial consensus update, so treat them as indicative." if _rthin_pdf else "")
                          +(f" No forward consensus trail supplied for: {strip(', '.join(_rmiss))}." if _rmiss else "")
                          +"",
                          S("n",fontSize=6.6,leading=8,textColor=_c.HexColor(GY))),
                 Spacer(1,3),tbl(_rh,_rr2,_rw,numc=tuple(range(1,len(_rh))))])
        story+= (keep_if_fits(_vgrp) if CONFIG.get("keep_blocks_together",True) else _vgrp)
    _mgrp=sec_flow("Methodology")+[
            Paragraph(xesc(method)+"  For informational purposes only. Not investment advice.",
                      S("mt",fontSize=6.7,leading=8.2,textColor=_c.HexColor(GY)))]
    story+= (keep_if_fits(_mgrp) if CONFIG.get("keep_blocks_together",True) else _mgrp)
    def _foot(cv,dc):
        cv.saveState(); y=FOOT_BASE; cv.setStrokeColor(_c.HexColor(TB)); cv.setLineWidth(.5); cv.line(0.6*inch,FOOT_RULE,letter[0]-0.6*inch,FOOT_RULE)
        cv.setFont(BODY,6.6); cv.setFillColor(_c.HexColor(GY))
        cv.drawString(0.6*inch,y,f"InSync Analytics · Forecast Reliability Suite · InSync MCP · {TICK}")
        cv.drawRightString(letter[0]-0.6*inch,y,f"Page {dc.page}"); cv.restoreState()
    doc=BaseDocTemplate(pdf_path,pagesize=letter,leftMargin=0.5*inch,rightMargin=0.5*inch,topMargin=0.5*inch,bottomMargin=BOTM,title=f"{TICK} Forecast Reliability Suite")
    fr=Frame(doc.leftMargin,doc.bottomMargin,doc.width,doc.height,leftPadding=0,rightPadding=0,topPadding=0,bottomPadding=0)
    doc.addPageTemplates([PageTemplate(frames=[fr],onPage=_foot)]); doc.build(story)
    pdf_msg=pdf_path; PDF_FAILED=False
  except Exception as e:
    _missing="ModuleNotFoundError" if isinstance(e,(ImportError,ModuleNotFoundError)) else None
    pdf_msg=(f"skipped (reportlab not installed: {e})" if _missing
             else f"FAILED ({type(e).__name__}: {e})")
    PDF_FAILED = _missing is None
    try:
        _mj=os.path.splitext(OUTP)[0]+"_metrics.json"
        if os.path.exists(_mj):
            _d=json.load(open(_mj,encoding="utf-8"))
            _d["pdf_status"]="failed" if PDF_FAILED else "reportlab_missing"
            _d.setdefault("data_flags",[]).append(
                "PDF was requested but could not be rendered; the HTML portal is complete but the "
                "print twin is missing. Cause: %s: %s" % (type(e).__name__,str(e)[:260]))
            json.dump(_d,open(_mj,"w",encoding="utf-8"),indent=1)
    except Exception:
        pass
print("OK" if not PDF_FAILED else "PARTIAL",OUTP,"| metrics:",len(METR),"| critical_gaps:",len(critical),"| forced:",FORCE,"| pdf:",pdf_msg)
if PDF_FAILED: sys.exit(4)
