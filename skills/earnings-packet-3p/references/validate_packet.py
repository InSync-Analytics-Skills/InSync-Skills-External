#!/usr/bin/env python3
"""earnings-packet-3p, the six validation passes. Run from the packet working dir.

    python3 references/validate_packet.py "<TICKER> <Q> Earnings Packet.pdf"
    python3 references/validate_packet.py --lenient-prose "<...>.pdf"  # escape, see pass 3

Pass 1  colour        every coloured numeric cell obeys green=positive / red=negative,
                      and no negative number renders black
Pass 2  geometry      every table shares one left edge and one right edge, on every page
Pass 3  prose         every number written in packet_narrative.json exists in
                      packet_computed.json, the no-fabrication gate
Pass 4  page count    the packet is within its page band
Pass 5  dash columns  no rendered column is majority em-dash, an empty column is a
                      layout failure that the renderer's prune should have caught
Pass 6  pagination    no table fragment sits alone at the top of a page; a table that
                      splits leaves at least MIN_ROWS rows on each side of the break

Exit code is non-zero if any pass fails.  A packet that fails validation does not ship.
"""
import io, json, re, sys, collections, decimal

STRICT_PROSE = '--lenient-prose' not in sys.argv
LENIENT_PROSE = '--lenient-prose' in sys.argv
PRECISION_IS_VERDICT = False
STRICT_PRECISION = PRECISION_IS_VERDICT or '--strict-precision' in sys.argv

MIN_ROWS = 3
DASH_MIN = 3
DASH_MAX = 0.50

GN=(0x1E/255,0x7A/255,0x34/255); RD=(0xB0/255,0x2A/255,0x1F/255)
def _ccy_alt():
    import json as _json, os as _os
    here = _os.path.dirname(_os.path.abspath(__file__))
    try:
        with open(_os.path.join(here, "..", "common", "currency-tokens.json"),
                  encoding="utf8") as fh:
            toks = _json.load(fh)["tokens"]
    except (OSError, ValueError, KeyError):
        toks = ["$", "€", "£", "₹"]
        print("warn: common/currency-tokens.json unreadable, money matching is reduced")
    return "(?:" + "|".join(re.escape(t) for t in toks) + ")"


CCY_ALT = _ccy_alt()
NUM=re.compile(r'^[+–\-]?\(?' + CCY_ALT + r'?[+–\-]?[0-9][0-9,]*(\.[0-9]+)?([%xBM])?\)?( bps)?$')

_LEAD = '([$\u20ac\u00a3\u20b9'


def _is_neg(t):
    return t.startswith('(') or t.lstrip(_LEAD)[:1] in ('\u2013', '-')

def _near(c,t,tol=0.02):
    r=((c>>16)&255)/255,((c>>8)&255)/255,(c&255)/255
    return all(abs(a-b)<tol for a,b in zip(r,t))

def _negok_forms():
    try:
        with open('packet_computed.json', encoding='utf8') as fh:
            C = json.load(fh)
    except (OSError, ValueError):
        return set()
    out = set()

    def _forms(x, d):
        q = decimal.Decimal(1).scaleb(-d)
        half_up = decimal.Decimal(repr(x)).quantize(q, rounding=decimal.ROUND_HALF_UP)
        return {f'{half_up:,.{d}f}', f'{x:,.{d}f}'}

    _scale = ((C.get('meta') or {}).get('money_scale') or 'B')

    def emit(v, fmt):
        if not isinstance(v, (int, float)) or isinstance(v, bool) or v >= 0:
            return
        a = abs(v)
        if fmt == '$M':
            out.update(_forms(a / 1000.0, 2))
            out.update(_forms(a, 1))
        elif fmt == '$':
            out.update(_forms(a, 2))
        elif fmt == '$3':
            out.update(_forms(a, 3))
        elif fmt == '%':
            out.update(_forms(a, 1))
        elif fmt == '%2':
            out.update(_forms(a, 2))
        elif fmt == 'x':
            out.update(_forms(a, 2))
        elif fmt == 'k':
            out.update(_forms(a * 1000, 0))
        elif fmt == '#B':
            out.update(_forms(a, 3))
        else:
            out.update(_forms(a, 1))

    for row in C.get('model') or []:
        if isinstance(row, dict) and row.get('neg_ok'):
            for v in (row.get('cells') or {}).values():
                emit(v, row.get('fmt'))
    for row in C.get('dispersion') or []:
        if isinstance(row, dict) and row.get('neg_ok'):
            for k in ('mean', 'min', 'max'):
                emit(row.get(k), row.get('fmt'))
    keys = [c.get('key') for c in (C.get('rev_cols') or []) if c.get('key')]
    for row in C.get('revisions') or []:
        if isinstance(row, dict) and row.get('neg_ok'):
            for k in keys:
                emit(row.get(k), row.get('fmt'))
    return out


_DIGITS = re.compile(r'[^0-9.,]')


def pass_colour(doc):
    bad=[]; blackneg=[]; g=r=0
    negok=_negok_forms(); exempt=0
    for pno,page in enumerate(doc,1):
        for b in page.get_text('dict')['blocks']:
            for l in b.get('lines',[]):
                for sp in l['spans']:
                    t=sp['text'].strip()
                    if not t or not NUM.match(t): continue
                    neg=_is_neg(t)
                    if _near(sp['color'],GN):
                        g+=1
                        if neg: bad.append((pno,t,'green but negative'))
                    elif _near(sp['color'],RD):
                        r+=1
                        if not neg: bad.append((pno,t,'red but positive'))
                    elif neg:
                        if _DIGITS.sub('',t) in negok: exempt+=1
                        else: blackneg.append((pno,t))
    info=f'{g} green / {r} red'
    if exempt: info+=f' / {exempt} in ink on a neg_ok row'
    return bad+[(p,t,'negative rendered black') for p,t in blackneg], info

def pass_geometry(doc):
    tables=[]
    for page in doc:
        vs=sorted((round(x['rect'].y0,1),round(x['rect'].x0,1))
                  for x in page.get_drawings() if x['rect'].height>2 and x['rect'].width<2)
        groups=[]
        for y,x in vs:
            for q in groups:
                if abs(q['y']-y)<6: q['xs'].append(x); break
            else: groups.append({'y':y,'xs':[x]})
        tables += [(min(q['xs']),max(q['xs'])) for q in groups if len(q['xs'])>2]
    lefts=collections.Counter(l for l,_ in tables); rights=collections.Counter(r for _,r in tables)
    bad=[]
    if len(lefts)>1:  bad.append(('left edges differ', dict(lefts)))
    if len([k for k,v in rights.items() if v>1])>1: bad.append(('right edges differ', dict(rights)))
    return bad, f'{len(tables)} tables'

def pass_prose():
    try:
        C=json.load(open('packet_computed.json')); N=json.load(open('packet_narrative.json'))
    except FileNotFoundError as e:
        return ([('setup', f'{e.filename} not found: run this from the packet working '
                           f'directory, the one holding packet_computed.json')], 'not run')
    hay=json.dumps(C)
    FIG=set(C.get('narrative_figures') or [])
    _NUMPART = re.compile(r'^[+–-]?' + CCY_ALT + r'?\s*(\d[\d,]*(?:\.\d+)?)')

    def _norm(x):
        m = _NUMPART.match(str(x).strip())
        if m:
            return m.group(1).replace(',', '')
        t = str(x).strip().lower()
        return t.lstrip('+–-($ ').rstrip('%x)b .,').replace(',', '')
    DOC={_norm(x) for x in (N.get('document_figures') or [])}
    DOC.discard('')
    PRN = set()
    try:
        with io.open('printed_figures.json', encoding='utf-8') as fh:
            PRN = {str(x) for x in (json.load(fh).get('printed') or [])}
    except Exception:
        PRN = set()
    _PRN_FLAT = {p.replace(',', '') for p in PRN}
    _MONEY_SCALE = ((C.get('meta') or {}).get('money_scale') or 'B')

    def _bare(t):
        t = str(t).strip().lstrip('+–-').lstrip('$€£¥')
        return t.rstrip('%xXbB) ').rstrip('.,').replace(',', '')

    def _unit(t):
        t = str(t).strip()
        if t.endswith('%'): return '%'
        if t.endswith('x') or t.endswith('X'): return 'x'
        if t.endswith('B') or t.endswith('M'): return '$scale'
        if t.lstrip('+–-')[:1] in ('$', '€', '£', '¥'): return '$'
        return ''

    PRN_BARE = {}
    for pstr in PRN:
        b = _bare(pstr)
        if b:
            PRN_BARE.setdefault((_unit(pstr), b), set()).add(pstr)
    PRN_BY_VALUE = {}
    for pstr in PRN:
        b = _bare(pstr)
        try: v = float(b)
        except ValueError: continue
        PRN_BY_VALUE.setdefault((_unit(pstr), round(v, 6)), set()).add(pstr)

    def _dec(b):
        return len(b.split('.')[1]) if '.' in b else 0

    def _hu(x, d):
        q = decimal.Decimal(1).scaleb(-d)
        return float(decimal.Decimal(repr(x)).quantize(q, rounding=decimal.ROUND_HALF_UP))

    def precision_mismatch(tok):
        if not PRN: return None
        u, b = _unit(tok), _bare(tok)
        if not b: return None
        if _norm(tok) in DOC: return None
        try: v = float(b)
        except ValueError: return None

        for (pu, pb) in PRN_BARE:
            if pu != u: continue
            if pb == b: return None
            try:
                if float(pb) == v: return None
            except ValueError: continue

        if _dec(b) >= 2:
            d = _dec(b) - 1
            for _r in (_hu(v, d), round(v, d)):
                forms = PRN_BY_VALUE.get((u, round(_r, 6)))
                if forms: return sorted(forms)[0]

        if u == '' and abs(v) >= 1000:
            for d in range(0, 4):
                for _r in (_hu(v / 1000.0, d), round(v / 1000.0, d)):
                    forms = PRN_BY_VALUE.get(('$scale', round(_r, 6)))
                    if forms: return sorted(forms)[0]
        return None

    def known_strict(tok):
        core=tok.lstrip('+–-($').rstrip('%x)B ').rstrip('.,').replace(',','')
        if not core: return True
        if core in DOC or _norm(tok) in DOC: return True
        if re.fullmatch(r'(19|20|21)\d\d', core): return True
        return core in FIG or core.lstrip('0') in FIG or ('0'+core) in FIG
    def known_legacy(tok):
        core=tok.lstrip('+–-($').rstrip('%x)B ').replace(',','')
        if not core or core in ('0','1','2','3','4','5'): return True
        if core in hay: return True
        try: v=float(core)
        except ValueError: return True
        for cand in (v, v*1000, v/1000):
            for d in range(0,5):
                if f'{cand:.{d}f}' in hay: return True
        return False
    strict_ready = STRICT_PROSE and bool(FIG)
    if STRICT_PROSE and not FIG:
        return ([('pass 3', 'narrative_figures',
                  'the figure set has not been published, so the no-fabrication gate cannot '
                  'run. Run  compute.py --figures  and re-run, or pass --lenient-prose to '
                  'accept the old substring test for this run')],
                'figure set absent')
    known = known_strict if strict_ready else known_legacy
    MONTH=(r'(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?'
           r'|Aug(?:ust)?|Sep(?:t|tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)')
    NOISE=re.compile(r'S&P\s+500|\b[A-Z]{2}_[A-Z]_\d+|\b[A-Z]{2}_[A-Z]{1,2}_\d+'
                     r'|\b\d{1,2}\s+' + MONTH + r'\b'
                     r'|\b' + MONTH + r'\s+\d{1,2}\b')
    TOKEN=re.compile(r'[+–-]?\$?\d[\d,]*\.?\d*(?:%|x)?')
    def scan(txt):
        return TOKEN.findall(NOISE.sub(' ', str(txt)))
    bad=[]; advisory=[]; precision=[]

    MONEY_B = re.compile(r'\$\d[\d,]*(?:\.\d+)?B\b')

    def _scan_surface(where, txt):
        if _MONEY_SCALE == 'M':
            for mb in MONEY_B.findall(NOISE.sub(' ', str(txt))):
                if mb.replace(',', '') not in _PRN_FLAT and _norm(mb) not in DOC:
                    bad.append((where, mb + ' (the page prints money in $M)'))
        for tok in scan(txt):
            if not known(tok): bad.append((where, tok))
            if FIG and known is known_legacy and not known_strict(tok):
                advisory.append((where, tok))
            pm = precision_mismatch(tok)
            if pm is not None: precision.append((where, tok, pm))
    for key,txt in list(N.get('notes',{}).items()):
        _scan_surface(f'notes.{key}', txt)
    for i,p in enumerate(N.get('what_matters',[]),1):
        _scan_surface(f'what_matters[{i}]', p['detail'])
    for arr in ('risks', 'catalysts'):
        for i,it in enumerate(N.get(arr,[]),1):
            _scan_surface(f'{arr}[{i}]', it.get('text',''))
    info=(f'{len(N.get("notes",{}))} notes, {len(N.get("what_matters",[]))} pointers, '
          f'{len(N.get("risks",[]))+len(N.get("catalysts",[]))} section 10 items')
    if LENIENT_PROSE:
        info += '; --lenient-prose: the SUBSTRING test was the verdict, which enforces ' \
                'almost nothing. Rule 1 is on your discipline for this build'
    if FIG:
        shortfall = bad if strict_ready else advisory
        info += (f'; strict set of {len(FIG)} figures, '
                 + ('VERDICT: ' if strict_ready else 'advisory: ')
                 + (f'{len(shortfall)} token(s) refused' if strict_ready
                    else f'{len(shortfall)} token(s) it would refuse'))
        advisory = shortfall
        if advisory:
            shown = advisory[:12]
            verb = 'REFUSED     ' if strict_ready else 'would refuse'
            info += ':\n' + '\n'.join(f'         {verb}  {w}: {t!r}'
                                       for w, t in shown)
            if len(advisory) > len(shown):
                info += f'\n         ... and {len(advisory)-len(shown)} more'
            info += ('\n         each is a figure to place in packet_computed.json or to '
                     'declare in narrative document_figures')
    if not PRN:
        msg = ('; the printed set is absent, so the precision check did NOT run. Run '
               'render_packet.py (it writes printed_figures.json) and re-run')
        if STRICT_PRECISION:
            return ([('pass 3', 'printed_figures',
                      'the printed set has not been written, so the precision check cannot '
                      'run under --strict-precision. Run render_packet.py and re-run')],
                    'printed set absent')
        info += msg
    if precision:
        shown = precision[:12]
        verb = 'MISMATCH    ' if STRICT_PRECISION else 'precision   '
        info += (f'; {len(precision)} precision mismatch(es)' + ':\n'
                 + '\n'.join(f'         {verb}  {w}: wrote '
                              f'{t.rstrip(chr(44)+chr(46))!r} where the page '
                              f'prints {pm!r}' for w, t, pm in shown))
        if len(precision) > len(shown):
            info += f'\n         ... and {len(precision)-len(shown)} more'
        info += ('\n         the value is right and the FORM is not: quote the string the '
                 'table prints, or the reader sees two numbers')
    if STRICT_PRECISION:
        bad = list(bad) + [(w, t) for w, t, _ in precision]
    elif precision and not bad:
        info += '\n         (reported, not a failure: run with --strict-precision to fail on it)'
    return bad, info

def _blocks(page):
    vs=[x['rect'] for x in page.get_drawings() if x['rect'].height>2 and x['rect'].width<2]
    blocks=[]
    for r in sorted(vs,key=lambda r:(round(r.y0,1),r.x0)):
        for b in blocks:
            if r.y0 < b['y1']-1 and r.y1 > b['y0']+1:
                b['y0']=min(b['y0'],r.y0); b['y1']=max(b['y1'],r.y1); b['xs'].append(r.x0); break
        else: blocks.append({'y0':r.y0,'y1':r.y1,'xs':[r.x0]})
    return [b for b in blocks if len(b['xs'])>2]

_DASH_FALLBACK=['','-','–','—','–%','-%','–x','n/a','N/A']
def _dash_vocabulary():
    import json as _json, os as _os
    here = _os.path.dirname(_os.path.abspath(__file__))
    for cand in (_os.path.join(here, "..", "common", "empty-vocabulary.json"),
                 _os.path.join(here, "common", "empty-vocabulary.json")):
        try:
            with open(cand, encoding="utf8") as fh:
                v = _json.load(fh)
            got = {e["text"] for e in v.get("entries", []) if e.get("renders_as_dash")}
            if got:
                return got
        except (OSError, ValueError, KeyError, TypeError):
            pass
    return set(_DASH_FALLBACK)
DASHES=_dash_vocabulary()

def pass_dashes(doc):
    bad=[]; ncols=0
    for pno,page in enumerate(doc,1):
        spans=[(sp['text'].strip(),(sp['bbox'][0]+sp['bbox'][2])/2,(sp['bbox'][1]+sp['bbox'][3])/2)
               for b in page.get_text('dict')['blocks'] for l in b.get('lines',[]) for sp in l['spans']
               if sp['text'].strip()]
        for b in _blocks(page):
            edges=[]
            for x in sorted(set(round(v,1) for v in b['xs'])):
                if not edges or x-edges[-1]>3: edges.append(x)
            if len(edges)<3: continue
            bins=[{'dash':0,'val':0} for _ in range(len(edges)-1)]
            for t,cx,cy in spans:
                if not (b['y0']-1 <= cy <= b['y1']+1): continue
                for i in range(len(edges)-1):
                    if edges[i] <= cx < edges[i+1]:
                        if t in DASHES:    bins[i]['dash']+=1
                        elif NUM.match(t): bins[i]['val']+=1
                        break
            for i,c in enumerate(bins):
                n=c['dash']+c['val']
                if not n: continue
                ncols+=1
                if c['dash']>=DASH_MIN and c['dash']/n > DASH_MAX:
                    bad.append((f'page {pno}',
                                f'column {i+1} of the table at y={round(b["y0"])}: '
                                f'{c["dash"]} dashes of {n} cells'))
    return bad, f'{ncols} columns scanned'

def pass_pagination(doc):
    bad=[]
    for pno,page in enumerate(doc,1):
        bs=_blocks(page)
        if not bs: continue
        top=min(b['y0'] for b in bs)
        for b in bs:
            hs=sorted(set(round(x['rect'].y0,1) for x in page.get_drawings()
                          if x['rect'].width>20 and x['rect'].height<2
                          and b['y0']-1 <= x['rect'].y0 <= b['y1']+1))
            rows=max(0,len(hs)-1)
            head = pno>1 and abs(b['y0']-top)<10
            tail = b['y1'] > page.rect.y1-70
            if rows and rows<MIN_ROWS and (head or tail):
                bad.append((f'page {pno}', f'{rows}-row table fragment at y={round(b["y0"])}'))
    return bad, f'{sum(len(_blocks(p)) for p in doc)} table blocks'

def main():
    try: import fitz
    except ImportError:
        print('pymupdf required:  pip install pymupdf'); return 2
    args=[a for a in sys.argv[1:] if not a.startswith('-')]
    pdf=args[0] if args else None
    if not pdf: print(__doc__); return 2
    doc=fitz.open(pdf)
    fails=0
    for name,(bad,info) in [('1 colour',pass_colour(doc)),
                            ('2 geometry',pass_geometry(doc)),
                            ('3 prose',pass_prose()),
                            ('5 dashes',pass_dashes(doc)),
                            ('6 pagination',pass_pagination(doc))]:
        status='PASS' if not bad else 'FAIL'
        print(f'pass {name:12} {status}   ({info})')
        for b in bad[:20]: print('        ', b)
        fails += bool(bad)
    n=doc.page_count
    ok = 3 <= n <= 4
    print(f'pass 4 pages       {"PASS" if ok else "FAIL"}   ({n} pages; band is 3-4)')
    if n == 4:
        print('        WARNING: three pages is the specification. Compress by structure, '
              'never by deleting a section: see references/house-format.md.')
    fails += (not ok)
    print('\nVALIDATION', 'PASSED' if not fails else f'FAILED ({fails} pass(es))')
    return 0 if not fails else 1

if __name__=='__main__': sys.exit(main())
