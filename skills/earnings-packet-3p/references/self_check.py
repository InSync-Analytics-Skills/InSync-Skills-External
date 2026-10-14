#!/usr/bin/env python3
"""earnings-packet-3p, self test - the Python twin of self_check.js.

    python3 references/self_check.py [<rendered>.pdf]

Verifies the environment and, critically, that the renderer holds no ticker, sector, period or
prose literal, the property that makes it portable. Handed a rendered PDF it also runs the
masthead geometry check; run from a packet working directory it renders twice and compares
the page (the determinism test).

Python standard library only, and no node: it checks the PYTHON renderer and tools
(render_packet.py, fmt.py, check_contract.py, check_table_widths.py) wherever self_check.js
checks the JavaScript ones, through the same assertions spelled in Python. Every other check,
its message and the pass/fail logic are the JavaScript's. The checks that are about JavaScript
itself are replaced by their Python equivalents; they are listed in SUBSTITUTIONS below and are
the only messages that differ. Where SKILL.md is checked for the command that runs a gate, both
the current `node ... .js` form and the `python3 ... .py` form are accepted.

maintainer/port_check_tools.py compares the two over every fixture and the failure paths.
"""
import ast
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import tokenize
import traceback
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
VERBOSE = os.environ.get('PACKET_VERBOSE') == '1'
FX = os.path.isdir(os.path.join(HERE, 'fixtures'))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
from fmt import JSError, type_error, G, jt, jor, nn, jstr, need, UNDEF, utf16len
from jscompat import js_keys, _js_read_json, _JSErr

SUBSTITUTIONS = (
    ("node >= 18 (<version>)", "python3 >= 3.8 (<version>)"),
    ("docx module present / docx module missing: npm i docx",
     "zlib module present / zlib module missing: the renderer writes the .docx with it"),
    ("docx Document constructs / docx Document failed: <e>",
     "render_packet.py loads / render_packet.py failed to load: <e>"),
    ("python3 <module> probe runs `python3` from PATH", "probe runs this interpreter (sys.executable)"),
    ("bundled: references/fmt.js, render_packet.js, check_contract.js, check_table_widths.js",
     "bundled: references/fmt.py, render_packet.py, check_contract.py, check_table_widths.py, jscompat.py"),
    ("render_packet.js reads the shared empty vocabulary", "render_packet.py reads the shared empty vocabulary"),
    ("skip  masthead geometry - ... node self_check.js <out.pdf>", "... python3 self_check.py <out.pdf>"),
    ("every colour render_packet.js draws with ...", "every colour render_packet.py draws with ..."),
    ("... names the asset render_packet.js actually loads", "... names the asset render_packet.py actually loads"),
    ("SKILL.md invokes check_table_widths.js", "SKILL.md invokes check_table_widths.py (either form accepted)"),
    ("SKILL.md invokes self_check.js WITH a rendered PDF", "SKILL.md invokes self_check.py WITH a rendered PDF (either form)"),
    ("B1: fmt.js carries the money scale ...", "B1: fmt.py carries the money scale ..."),
    ("determinism renders with node render_packet.js", "determinism renders with python3 render_packet.py"),
)


_WS = '\t\n\x0b\x0c\r \xa0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000\ufeff'


def jtrim(s):
    return s.strip(_WS)


def _utf8(s):
    return s.encode('utf-16', 'surrogatepass').decode('utf-16', 'replace').encode('utf-8')


def out(s):
    sys.stdout.buffer.write(_utf8(s + '\n'))


def P(*parts):
    return os.path.normpath(os.path.join(*parts))


def rd(p):
    if os.path.isdir(p):
        raise JSError('Error', 'EISDIR: illegal operation on a directory, read')
    try:
        with open(p, 'rb') as f:
            return f.read().decode('utf-8', 'replace')
    except FileNotFoundError:
        raise JSError('Error', "ENOENT: no such file or directory, open '%s'" % p)
    except PermissionError:
        raise JSError('Error', "EACCES: permission denied, open '%s'" % p)


def parse(p):
    rd(p)
    try:
        return _js_read_json(p)
    except _JSErr as e:
        raise JSError('SyntaxError', str(e))


def S(pat, s, flags=0):
    return re.search(pat, s, flags) is not None


def js_join(a, sep=','):
    return sep.join('' if nn(x) else jstr(x) for x in a)


def js_slice(s, a, b):
    n = len(s)
    a = max(0, n + a) if a < 0 else min(a, n)
    b = max(0, n + b) if b < 0 else min(b, n)
    return s[a:b] if b > a else ''


def values(o):
    if isinstance(o, dict):
        return [o[k] for k in js_keys(o)]
    if isinstance(o, list):
        return list(o)
    if isinstance(o, str):
        return list(o)
    if nn(o):
        raise type_error('Cannot convert undefined or null to object')
    return []


def strip_docstring(src):
    try:
        mod = ast.parse(src)
    except SyntaxError:
        return src
    if mod.body and isinstance(mod.body[0], ast.Expr) and isinstance(getattr(mod.body[0], 'value', None), ast.Constant) \
            and isinstance(mod.body[0].value.value, str):
        lines = src.split('\n')
        return '\n'.join(lines[mod.body[0].end_lineno:])
    return src


def prose_of(src, drop_line):
    lines = src.split('\n')
    keep = []
    try:
        toks = list(tokenize.generate_tokens(io.StringIO(src).readline))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return '\n'.join(l for l in lines if not drop_line(l))
    first = True
    for t in toks:
        if t.type in (tokenize.ENCODING, tokenize.NL, tokenize.NEWLINE, tokenize.INDENT, tokenize.DEDENT,
                      tokenize.ENDMARKER):
            continue
        if t.type == tokenize.COMMENT:
            continue
        if first and t.type == tokenize.STRING:
            first = False
            continue
        first = False
        if drop_line(lines[t.start[0] - 1]):
            continue
        keep.append(t.string)
    return ' '.join(keep)


def colour_literals(src):
    found = []
    try:
        toks = tokenize.generate_tokens(io.StringIO(src).readline)
        for t in toks:
            if t.type == tokenize.STRING:
                try:
                    v = ast.literal_eval(t.string)
                except Exception:
                    continue
                if isinstance(v, str) and re.fullmatch('[0-9A-Fa-f]{6}', v):
                    found.append(v.upper())
    except (tokenize.TokenError, IndentationError, SyntaxError):
        pass
    return list(dict.fromkeys(found))


def py_const(src, name):
    try:
        mod = ast.parse(src)
    except SyntaxError:
        return None
    for node in ast.walk(mod):
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        tg, v = node.targets[0], node.value
        if isinstance(tg, ast.Name) and tg.id == name and isinstance(v, ast.Constant) and isinstance(v.value, str):
            return v.value
        if isinstance(tg, ast.Tuple) and isinstance(v, ast.Tuple) and len(tg.elts) == len(v.elts):
            for a, b in zip(tg.elts, v.elts):
                if isinstance(a, ast.Name) and a.id == name and isinstance(b, ast.Constant) and isinstance(b.value, str):
                    return b.value
    return None


def func_src(src, name):
    try:
        mod = ast.parse(src)
    except SyntaxError:
        return ''
    for node in ast.walk(mod):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_source_segment(src, node) or ''
    return ''


def main(argv):
    st = {'fail': 0}

    def ok(c, m, *_extra):
        if c and not VERBOSE:
            st['ok'] = st.get('ok', 0) + 1
            return
        out(('  ok  ' if c else '  FAIL') + '  ' + m)
        if not c:
            st['fail'] += 1

    v = sys.version_info
    ok(v >= (3, 8), 'python3 >= 3.8 (%d.%d.%d)' % (v[0], v[1], v[2]))
    try:
        import zlib
        ok(True, 'zlib module present')
    except ImportError:
        ok(False, 'zlib module missing: the renderer writes the .docx with it')
    for mod, why in (('pymupdf', 'validator passes 1-6 and validate_rules_style'),
                     ('pdfplumber', 'the masthead geometry check')):
        try:
            r = subprocess.run([sys.executable, '-c', 'import ' + mod], stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL)
            status = r.returncode
        except OSError:
            status = None
        ok(status == 0, 'python3 ' + mod + ' present, needed by ' + why
           + ('' if status == 0 else '  ->  pip install ' + mod))
    for f in ('fmt.py', 'render_packet.py', 'validate_packet.py', 'check_contract.py',
              'validate_rules_style.py', 'verify_delivered.py', 'check_table_widths.py',
              'check_masthead.py', 'compute.py', 'jscompat.py'):
        ok(os.path.exists(P(HERE, f)), 'bundled: references/' + f)
    ok(os.path.exists(P(HERE, '..', 'assets', 'insync-logo.jpg')), 'bundled: assets/insync-logo.jpg')

    src = rd(P(HERE, 'render_packet.py'))
    body = strip_docstring(src)
    ok(not S('[\'"][1-4]Q[0-9][0-9][\'"]', body), 'no hardcoded quarter literal')
    ok(not S('[\'"](?:19|20)[0-9][0-9][\'"]', body), 'no hardcoded year literal')
    ok(not S('\\b(WMT|AAPL|MSFT|JPM)\\b', body, re.A), 'no hardcoded ticker')

    ok(not S('[\'"][^\'"]*\\b[1-4]Q[0-9][0-9]\\b[^\'"]*[\'"]', body, re.A),
       'no quarter literal embedded in a longer string')
    hits = list(dict.fromkeys(m.group(0) for m in re.finditer(
        '\\b(LTCODE|IS_P_[0-9]+|CF_P_[0-9]+|CF_S_[0-9]+|BS_P_[0-9]+|SD_[SP]_[A-Za-z0-9_.]+|KM_[SP]_[0-9]+|RT_P_[0-9]+)\\b',
        body, re.A)))
    ok(len(hits) == 0, 'no line-item code in the renderer' + (': ' + ' '.join(hits[:6]) if hits else ''))
    money = S('[\'"][^\'"]*\\$[0-9][0-9,.]*\\s*[\u2013-]\\s*\\$?[0-9][0-9,.]*[^\'"]*[\'"]', body)
    ok(not money, 'no guided range literal')
    sectors = S("'(staples retail|banking|insurance|reit|utilities|software_growth|energy_materials)'", body, re.I)
    ok(not sectors, 'no sector name literal')

    prose = prose_of(src, lambda l: bool(re.match('\\s*(EMPTY_FALLBACK = \\[|EMPTY_TXT = |_put\\()', l)))
    ok('\u2014' not in prose and not S('\\\\u2014', prose), 'no em dash in rendered prose')
    vp2 = P(HERE, '..', 'common', 'empty-vocabulary.json')
    if os.path.exists(vp2):
        vv = parse(vp2)
        ents = jor(G(vv, 'entries'), [])
        allv = [G(e, 'text') for e in need(ents, 'map', '(v.entries || []).map')]
        dash = [G(e, 'text') for e in need(jor(G(vv, 'entries'), []), 'filter', '(v.entries || []).filter')
                if jt(G(e, 'renders_as_dash'))]
        ok(len(allv) > 0 and len(dash) > 0 and len(dash) < len(allv),
           'empty vocabulary: the dash subset is a STRICT subset of the prune set ('
           + str(len(dash)) + ' of ' + str(len(allv)) + ')')
        ok(all(isinstance(G(e, 'why'), str) and utf16len(G(e, 'why')) > 10
               for e in need(jor(G(vv, 'entries'), []), 'every', '(v.entries || []).every')),
           'empty vocabulary: every entry records WHY it is on its side of the split')
        for f in ('render_packet.py', 'validate_packet.py', 'validate_rules_style.py'):
            ok(S('empty-vocabulary\\.json', rd(P(HERE, f))), f + ' reads the shared empty vocabulary, not a local set')
    else:
        ok(False, 'bundled: common/empty-vocabulary.json')

    for f in ('SKILL.md', 'README.md'):
        p = P(HERE, '..', f)
        if os.path.exists(p):
            ok('\u2014' not in rd(p), 'no em dash in ' + f)
    refs = P(HERE, '..', 'references')
    if os.path.exists(refs):
        bad = [f for f in os.listdir(refs) if f.endswith('.md') and '\u2014' in rd(P(refs, f))]
        ok(len(bad) == 0, 'no em dash in references' + (': ' + ', '.join(bad) if bad else ''))
    if os.path.exists(refs):
        bad = []
        for f in os.listdir(refs):
            if not re.search('\\.(js|py)\\Z', f):
                continue
            lines = rd(P(refs, f)).split('\n')
            hl = [str(i + 1) for i, l in enumerate(lines) if '\u2014' in l and 'EM_DASH_IS_DATA' not in l]
            if hl:
                bad.append(f + ':' + ','.join(hl))
        if FX:
            ok(len(bad) == 0, 'no em dash in the bundled scripts, outside the marked data lines'
               + (': ' + ' '.join(bad) if bad else ''))

    ok(not S('metric\\(\\s*[\'"][^\'"]*(P/E|EV/EBITDA|EV/Sales|P/B)', body),
       'no multiple named in the peer table by the renderer')
    ok(S("G\\(PR, 'val_metrics'\\)", body) and S('runBand\\(', body),
       'peer valuation band supplied by peers.json, built from the sector lens')
    ok(S("_note['\"]\\]", body) or S("\\+ ?'_note'", body),
       'a dropped peer multiple prints its reason rather than a number')

    ok(S("G\\(C, 'tiles'\\)", body) and not S('Consensus (EPS|revenue)[\'"]', body),
       'tile strip supplied by the computed file, not written into the renderer')
    ok(S('^TW = (CW|[0-9]+)', body, re.M), 'table-width invariant present')
    ok(S('^PW, PH, MG = [0-9]+, [0-9]+, [0-9]+\\s*\\nCW = PW - MG \\* 2', body, re.M),
       'text column derived from the page margin')
    ok(S('if t != TW:\\s*\\n\\s*raise', body), 'table-width invariant enforced')
    ok(S("G\\(N, 'what_matters'\\)", body) and S("G\\(G\\(N, 'notes'\\), '", body), 'prose read from the narrative file')
    ok(S("G\\(M, 'hist'\\)", body) and S("G\\(M, 'target'\\)", body), 'period keys derived from meta')
    ok(S('def sc\\(v\\):\\s*\\n\\s*return RD if \\(not nn\\(v\\) and js_lt\\(v, 0\\)\\) else DK', body),
       'negative-is-never-black rule present')

    ok(S('^KEEP_MAX = [0-9]+', body, re.M) and S('^MIN_BLOCK = [0-9]+', body, re.M), 'pagination constants present')
    ok(S('def keepFlags\\(', body) and S('keepNext=kn', body), 'keep-together applied to cell paragraphs')
    ok(S("tableHeader=bool\\(r\\.get\\('header'\\)\\)", body), 'header rows repeat across a page break')
    ok(S('def prune\\(', body) and S('minFill', body), 'empty-column prune present')
    ok(S('w, rows = prune\\(w, rows, opt\\)', body), 'prune runs inside T(), before the width check')
    ok(S('w\\[0\\] \\+= TW - sum\\(w\\)', body), 'pruned widths redistributed back to TW')
    ok(S('def dropNote\\(', body) and S("dropNote\\('", body), 'every prune is disclosed in a note')
    calls = re.findall('o\\.append\\(T\\([^\\n]*\\)\\)', body)
    bare = [c for c in calls if not S("\\{'(minFill|prune|keep)'", c)]
    ok(len(calls) > 0 and len(bare) == 0,
       'every T() call states its prune policy' + (': ' + str(len(bare)) + ' bare' if bare else ''))

    cc = rd(P(HERE, 'check_contract.py'))
    ok(S('packet_computed\\.json', cc) and S('peers\\.json', cc) and S('packet_narrative\\.json', cc),
       'contract check reads all three inputs')
    ok(S('render_packet\\.py', cc), 'contract check derives its field list from the renderer')

    val = rd(P(HERE, 'validate_packet.py'))
    ok(S('def pass_dashes', val) and S('def pass_pagination', val), 'validator passes 5 and 6 present')
    ok(S('MIN_ROWS *= *3', val) and S('^MIN_BLOCK = 3\\b', body, re.M),
       'validator MIN_ROWS matches renderer MIN_BLOCK')

    ok(S("^cb = \\{'top': NBB, 'bottom': RRB, 'left': NBB, 'right': NBB\\}", body, re.M),
       'tables carry horizontal rules only, no vertical gridlines')
    ok(S("MBOXB = \\{'top': MBB, 'bottom': MBB, 'left': MBB, 'right': MBB", body),
       'masthead carries the house outline box')
    ok(S("'insideHorizontal': \\{'style': NONE\\}", body),
       'masthead box draws no inside rules between its three cells')
    ok(S("MBRAND = jor\\(G\\(_MBJ, 'brand_size_half_points'\\)", body)
       and S("MBRAND2 = jor\\(G\\(_MBJ, 'brand_size_two_line_half_points'\\)", body),
       'both brand-line sizes are read from common/brand.json, not hardcoded')
    ok(S("MASTHEAD_FACE = 'Times New Roman'\\n", body) and S('MFT = [^\\n]*MASTHEAD_FACE\\n', body),
       'masthead face is Times New Roman, via the single MASTHEAD_FACE constant')
    bj = parse(P(HERE, '..', 'common', 'brand.json'))
    mh = G(bj, 'masthead')
    ov = jor(G(mh, 'face_overrides') if jt(mh) else mh, {})
    ok(G(ov, 'earnings-packet-3p') == 'Times New Roman' and isinstance(G(ov, 'earnings-packet-3p'), str),
       'brand.json declares the packet face override (got ' + jstr(jor(G(ov, 'earnings-packet-3p'), 'none')) + ')')
    ok(not S("\\+ ?'\\.\\.'", body) and not S("'\\.\\.' ?\\+", body),
       'no elision path survives in the masthead (shrink, never truncate)')
    ok(S('for z in \\(28, 27, 26, 25, 24\\):', body), 'company ladder runs 14pt -> a 12pt floor, then two lines')
    ok(S('textWidthPt\\(l, h / 2\\) <= _MBUD \\* 0\\.97', body), 'the fit test carries the 3% estimator headroom')
    exact = len(re.findall("'lineRule': 'exact'", body))
    ok(exact >= 2, 'every line of the masthead block carries an EXACT line rule (' + str(exact) + ')')
    mast_block = js_slice(body, body.find('# ---------- masthead'), body.find('# ---------- S1'))
    if FX:
        ok(len(re.findall("verticalAlign='bottom'", mast_block)) >= 2, 'mark cell and brand cell are both vAlign BOTTOM')
    ok(S('i = t\\.find\\(_MTK\\)', body), 'the (TICKER) token stays orange on whichever line the split puts it')
    ok(S('ZB if \\(body and bi % 2 == 1\\) else None', body), 'zebra fill counts body rows only')
    rg, rr = py_const(src, 'GN'), py_const(src, 'RD')
    hx = re.search('GN=\\(0x([0-9A-F]{2})/255,0x([0-9A-F]{2})/255,0x([0-9A-F]{2})/255\\)', val)
    hr = re.search('RD=\\(0x([0-9A-F]{2})/255,0x([0-9A-F]{2})/255,0x([0-9A-F]{2})/255\\)', val)
    rg = rg if rg and re.fullmatch('[0-9A-F]{6}', rg) else None
    rr = rr if rr and re.fullmatch('[0-9A-F]{6}', rr) else None
    j = lambda m: (m.group(1) + m.group(2) + m.group(3)).upper() if m else '?'
    ok(bool(rg and rr and hx and hr) and rg.upper() == j(hx) and rr.upper() == j(hr),
       'validator pass 1 palette matches the renderer GN/RD'
       + (' (renderer ' + rg + '/' + rr + ' vs validator ' + j(hx) + '/' + j(hr) + ')' if rg and rr else ''))
    rs = rd(P(HERE, 'validate_rules_style.py'))
    ok(S('MIN_ROWS *= *3', rs) and S('def pass_geometry', rs) and S('def pass_dashes', rs)
       and S('def pass_pagination', rs), 'rules-style validator re-derives passes 2, 5 and 6')

    try:
        if 'render_packet' not in sys.modules:
            import importlib
            importlib.import_module('render_packet')
        ok(True, 'render_packet.py loads')
    except Exception as e:
        ok(False, 'render_packet.py failed to load: ' + str(e))

    pdf = argv[0] if argv else None
    checker = os.path.join(HERE, 'check_masthead.py')
    if not pdf:
        out('  skip  masthead geometry - pass a rendered PDF: python3 self_check.py <out.pdf>')
    elif not os.path.exists(checker):
        out('  FAIL  masthead geometry - check_masthead.py missing')
        st['fail'] += 1
    else:
        try:
            r = subprocess.run([sys.executable, checker, pdf], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            so, status = r.stdout.decode('utf-8', 'replace'), r.returncode
        except OSError:
            so, status = '', None
        tail = jtrim(so).split('\n')[-1]
        if status == 0:
            out('  ok    masthead geometry - ' + tail)
        else:
            out('  FAIL  masthead geometry - ' + tail)
            st['fail'] += 1

    root = P(HERE, '..')
    try:
        brand = parse(P(root, 'common', 'brand.json'))
    except Exception:
        brand = None
    if not jt(brand):
        out('  warn  common/brand.json not readable, mirror check skipped')
        raise JSError('ReferenceError', 'warn is not defined')
    rp = rd(P(HERE, 'render_packet.py'))
    declared = set(x.upper() for x in values(jor(G(brand, 'colors'), {}))
                   if isinstance(x, str) and re.fullmatch('[0-9A-Fa-f]{6}', x))
    used = colour_literals(rp)
    undeclared = [h for h in used if h not in declared]
    ok(len(undeclared) == 0, 'every colour render_packet.py draws with is declared in common/brand.json')
    lf = jor(G(jor(G(brand, 'masthead'), {}), 'logo_file'), None)
    ok(jt(lf) and jstr(lf) in rp,
       'common/brand.json masthead.logo_file names the asset render_packet.py actually loads')

    sk = rd(P(root, 'SKILL.md'))
    rdm = rd(P(root, 'README.md'))
    fm = re.search('^version:\\s*"?([0-9]+\\.[0-9]+\\.[0-9]+)"?\\s*$', sk, re.M)
    fm = fm.group(1) if fm else None
    bd = re.search('\\*\\*Version:\\s*([0-9]+\\.[0-9]+\\.[0-9]+)\\*\\*', sk)
    bd = bd.group(1) if bd else None
    rm = re.search('^\\*Version\\s+([0-9]+\\.[0-9]+\\.[0-9]+)\\*\\s*$', rdm, re.M)
    rm = rm.group(1) if rm else None
    ok(bool(fm) and fm == bd and fm == rm, 'version consistent across frontmatter / SKILL body / README')
    bad = []
    for file, text in (('SKILL.md', sk), ('README.md', rdm)):
        for m in re.finditer('Earnings Packet v([0-9]+\\.[0-9]+\\.[0-9]+)', text):
            if m.group(1) != fm:
                bad.append(file + ' says v' + m.group(1))
    ok(len(bad) == 0, 'every "Earnings Packet v<version>" example matches the frontmatter version')

    cc = rd(P(HERE, 'check_contract.py'))
    ok(S("'quarterly'\\s*,\\s*'semi_annual'\\s*,\\s*'annual'", cc),
       'contract check knows all three axes (quarterly / semi_annual / annual)')
    ok(S('in PERIODICITY', cc), 'a supplied periodicity value is validated, not just permitted')
    ok(S('prior_label supplied for', cc),
       'a non-quarterly axis requires prior_label (section 4 otherwise names the wrong base)')
    pc = rd(P(HERE, 'pipeline-contracts.md'))
    ok(S("'quarterly' \\(default\\), 'semi_annual' or 'annual'", pc), 'pipeline contract documents all three axes')
    sk = rd(P(HERE, '..', 'SKILL.md'))
    ok(S('^annual\\s+the P&L returns only on bare-year labels', sk, re.M), 'SKILL.md documents the annual axis')

    sk10 = rd(P(HERE, '..', 'SKILL.md'))
    ok(S('## The run has TWO phases', sk10) and S('Phase B', sk10),
       'v7.10.0: SKILL.md declares the two-phase context split, which is what keeps the feed '
       + 'payload out of the build and verify turns')
    for r in ('references/verify.md', 'references/invariants.md', 'references/degradation.md',
              'references/maintaining.md', 'references/data-collection.md',
              'references/pipeline-contracts.md', 'references/sector-config.md',
              'references/house-format.md'):
        ok(r in sk10, 'v7.10.0: SKILL.md points at ' + r + ' (a reference nothing points at '
           + 'is a reference nobody opens, and the content that moved there is then lost)')
    inv = rd(P(HERE, 'invariants.md'))
    for frag in ('Sector drives content, never layout', 'No silent truncation',
                 'renderer holds no ticker', 'A table is a block',
                 'A column of dashes is a layout failure'):
        ok(frag in inv, 'v7.10.0: invariants.md carries the full statement of "' + frag
           + '" (the rule moved out of the prompt, it did not stop applying)')
    vfy = rd(P(HERE, 'verify.md'))
    ok(S('pass 7', vfy, re.I) and S('99,999\\.9B', vfy),
       'v7.10.0: verify.md carries the pass-by-pass detail including the demonstrated failure '
       + 'that only pass 7 catches')
    ok(S('steer the parse, do not parse then window', sk10) and S('`user_prompt` is the extraction', sk10),
       'v7.10.0: SKILL.md steers parse_document_content rather than parsing broadly and then '
       + 'windowing, which is the same prose paid for three times')
    ok(S('The probe IS the fetch', sk10),
       'v7.10.0: Step 2 probes on the REAL period grid and keeps the rows, rather than paying '
       + 'for the same actuals twice')
    ok(S('Cap the CANDIDATES, never the BANDS', sk10),
       'v7.10.0: the code cap applies to CANDIDATES and never to a sector band, because a code '
       + 'never fetched is a row that cannot be printed or disclosed')

    rp = rd(P(HERE, 'render_packet.py'))
    cm = rd(P(HERE, 'check_masthead.py'))
    ok(S('def productName\\(', rp) and S('productName\\(root\\) or ', rp),
       'renderer takes its product identity from SKILL.md, not the folder name')
    ok(S('def product_name\\(', cm) and S('product_name\\(root\\) or ', cm),
       'masthead checker takes its product identity from SKILL.md, not the folder name')
    sk = rd(P(HERE, '..', 'SKILL.md'))
    for g in (('check_table_widths.py', 'check_table_widths.js'), 'verify_delivered.py',
              'fixtures/run_matrix.py', 'fixtures/test_geometry_gate.py',
              'build_computed.py', 'fixtures/test_roundtrip.py',
              'sector.py', 'extract_doc_facts.py',
              'fixtures/test_scaffold.py', '--scaffold'):
        forms = g if isinstance(g, tuple) else (g,)
        ok(any(x in sk for x in forms), 'SKILL.md invokes ' + forms[0] + ' (a gate nothing calls is a gate nobody runs)')
    ok(S('build_computed\\.py --verify', sk),
       'SKILL.md invokes build_computed.py --verify, the round trip that tests the builder '
       + 'rather than the page (it shipped for several versions with no caller)')
    if FX:
        mt = rd(P(HERE, 'maintaining.md'))
        ok(not S('fixtures/test_[A-Za-z0-9_]+\\.py', mt),
           'maintaining.md carries no second copy of the release command list (it lives in '
           + "SKILL.md's \"Editing this skill\", which is the copy self_check reads)")
        ok(S('Editing this skill', mt), 'maintaining.md points at the one list rather than leaving the reader to find it')
    ok('--lenient-prose' in sk and S('pass 3 ENFORCES this', sk),
       'SKILL.md rule 1 states that pass 3 enforces it, and documents the escape')
    ok('empty-vocabulary.json' in sk,
       'SKILL.md rule 10 points at the shared vocabulary, not at two sets to keep in sync')
    va10 = rd(P(HERE, 'verify_all.py'))
    ok(S('self_check\\.(?:py|js)"?\\s+"?[^"\\n]*\\.pdf', sk)
       or (S('verify_all\\.py "<the built>\\.pdf"', sk)
           and S('"self_check\\.py"\\), pdf\\]', va10)),
       'SKILL.md invokes self_check.py WITH a rendered PDF, which is what arms the masthead '
       + 'geometry check and the determinism test')
    bc10 = rd(P(HERE, 'build_computed.py'))
    cfg10 = parse(P(HERE, 'sector_config.json'))
    mb = re.search('SCAFFOLD_MAP\\s*=\\s*\\{[\\s\\S]*?\\n\\}', bc10)
    map_block = mb.group(0) if mb else ''
    if nn(cfg10):
        raise type_error('Cannot convert undefined or null to object')
    missing = [k for k in (js_keys(cfg10) if isinstance(cfg10, dict) else [str(i) for i in range(len(cfg10))]
                           if isinstance(cfg10, list) else [])
               if not S('"' + k + '"\\s*:', map_block)]
    ok(len(missing) == 0, 'v7.10.0: every sector key in sector_config.json has a SCAFFOLD_MAP target'
       + (', missing: ' + ', '.join(missing) if missing else ''))
    ok(S('def scaffold\\(', bc10) and S('--scaffold', bc10), 'v7.10.0: build_computed.py implements --scaffold')
    vp = rd(P(HERE, 'validate_packet.py'))
    ok(S('except FileNotFoundError', vp), 'pass 3 reports a missing working directory rather than raising through main()')
    if FX:
        ok(S('READ ITS LIMITS', vp), "pass 3 documents that it is a substring test over the producer's own JSON")
    ok(S("LENIENT_PROSE = '--lenient-prose' in sys\\.argv", vp)
       and S("STRICT_PROSE = '--lenient-prose' not in sys\\.argv", vp),
       'pass 3 verdict defaults to the STRICT figure-set test, with --lenient-prose as the '
       + 'documented escape')
    ok(S('if STRICT_PROSE and not FIG', vp),
       'strict prose refuses to run on an unpublished figure set rather than refusing every token')
    cpy = rd(P(HERE, 'compute.py'))
    ok(S('R11', cpy) and S('combined ratio', cpy, re.I),
       'compute.py carries the feed-reconciliation identity that the PDF passes cannot see')
    ok(S('def figures\\(', cpy) and S('narrative_figures', cpy), 'compute.py publishes narrative_figures for the prose gate')
    ok(not S('open\\([^)]*[\'"]w[\'"]', cpy.split('def figures')[0]), 'compute.py --audit writes nothing')
    ok('compute.py --audit' in sk and 'compute.py --figures' in sk, 'SKILL.md Step 5 invokes both compute steps')
    ok(S('R14', cpy) and S('peers', cpy) and S('PR or \\{\\}', cpy),
       'compute.py audits peers.json, which Step 5 names as its responsibility')
    ok('references/restatement.py' in sk and S('restatement\\.py\\s*(#|$|\\n)', sk.replace('`', ''), re.M),
       'SKILL.md invokes restatement.py rather than only describing it')
    vp2 = rd(P(HERE, 'validate_packet.py'))
    ok(S('known = known_strict if strict_ready else known_legacy', vp2)
       and S("LENIENT_PROSE = '--lenient-prose' in sys\\.argv", vp2)
       and S('the figure set has not been published', vp2),
       'pass 3 enforces rule 1 by default, refuses to run on an unpublished figure set, and '
       + 'keeps --lenient-prose as a traceable escape')
    vd = rd(P(HERE, 'verify_delivered.py'))
    vd_imports = ' '.join(re.findall('^\\s*(?:import|from)\\s+\\S+', vd, re.M))
    ok(S('zipfile', vd_imports) and not S('(fmt|render_packet|check_|validate_)', vd_imports),
       'pass 7 reads the delivered document and imports nothing from this bundle')
    ok(S('"--strict" in sys\\.argv', vd), 'pass 7 is report-only unless --strict, so it cannot fail a correct build today')

    fmtj = rd(P(HERE, 'fmt.py'))
    rp4 = rd(P(HERE, 'render_packet.py'))
    bc4 = rd(P(HERE, 'build_computed.py'))
    cp4 = rd(P(HERE, 'compute.py'))
    cc4 = rd(P(HERE, 'check_contract.py'))
    vd4 = rd(P(HERE, 'verify_delivered.py'))
    pcm4 = rd(P(HERE, 'pipeline-contracts.md'))
    vp4 = rd(P(HERE, 'validate_packet.py'))
    sc3 = rd(P(HERE, 'sector-config.md'))
    ps3 = rd(P(HERE, 'packet-sections.md'))
    ok(S("if f == '%2':\\s*\\n\\s*return num\\(v, 2\\) \\+ '%'", fmtj),
       "rates: fmt '%2' prints a rate at two decimals, for a line that moves in basis points")
    ok(S('if f in \\("%", "%2"\\):', bc4) and S('if f in \\("%", "%2"\\):', cp4),
       "rates: '%2' is a RATE to both the builder and the audit, so its Y/Y is in basis points")
    ok(S('percentage rows where two materially different values print the same string', cc4),
       'rates: check_contract names any % row whose values collide at one decimal')
    ok(S('the house format is 1, or 2 ', vd4) and not S('a row carries one precision', vd4),
       'rates: pass 7 V5 admits 1 or 2 decimals PER CELL and makes no per-row assertion, '
       + 'which false-positived on a row holding levels beside a spread')
    ok(S('seen = \\{\\}', rp4) and S("sub-rows'", rp4),
       'dropNote: a repeated row label is counted, not listed once per occurrence')
    ok(S("NOTE_MIDSENTENCE = \\['peers_caveat', 'valuation_excluded_reason'\\]", cc4),
       'note case: exactly the two mid-sentence notes are exempted from the capital rule')
    ok(S('leading case matches the splice position', cc4),
       'note case: the gate reports by splice position and names every note that disagrees')
    ok(S('should start with a capital', cc4) and S('should start lowercase', cc4),
       'note case: the gate fails in BOTH directions, so neither a lowercase opener nor a '
       + 'capital mid-sentence can ship')
    ok(S('Leading case follows the splice position, and it is gated', pcm4)
       and S('`peers_read` takes no stop AND takes a capital', pcm4),
       'note case: documented as a rule over all notes, and documented as a set DIFFERENT '
       + 'from the terminal-stop set, which is why one set could not express both')
    if FX:
        ok(S('SECTION 10 IS SCANNED', vp4) and S("for arr in \\('risks', 'catalysts'\\)", vp4),
           'pass 3: section 10 risk and catalyst prose is scanned, not just notes and pointers')
    ok(S('section 10 items', vp4),
       'pass 3: the verdict line states how many section 10 items it read, so a gate that '
       + 'stops reading them is visible rather than silent')
    if FX:
        ok(S('take the leading numeric part and discard whatever unit follows it', vp4),
           'pass 3: a document_figures entry normalises to its LEADING NUMERIC PART, in whatever '
           + 'form the unit is written, so a declaration is matched by construction rather than '
           + 'by an enumerated list of unit words that a real packet outruns')
    if FX:
        ok(S('A DAY OF MONTH is not a figure', vp4),
           'pass 3: a day of month is exempted, so a dated catalyst needs no declaration')
    if FX:
        ok(os.path.exists(P(HERE, 'fixtures', 'test_prose_section10.py')),
           'pass 3: the section 10 line is held by a purpose-built gate test, because the base '
           + 'fixture cannot hold it')
    fm4 = rd(P(HERE, 'fmt.py'))
    ok(S("if f == 'x':\\s*\\n\\s*return xfmt\\(v\\)", fm4) and S('def xfmt\\(v\\)', fm4),
       'multiples: cell(v,"x") takes the house rule from one place, so a tile cannot print '
       + '14.57x beside a table cell reading 14.6x')
    ok(not S('num\\(v, 2 if js_lt\\(js_abs\\(v\\), 5\\) else 1\\)', rp4),
       'multiples: the renderer holds no second copy of the multiple rule')
    ok(S('2 if abs\\(v\\) < 5 else 1', bc4), 'multiples: a from-spec tile formats at the same rule the tables use')
    ok(S('^PRINTED = JSSet\\(\\)', fm4, re.M) and S('def record\\(s\\)', fm4),
       'printed set: collected inside the formatter, so it is what went on the page rather '
       + 'than a second implementation of it')
    ok(S('printed_figures\\.json', rp4) and S('printed_figures\\.json', vp4),
       'printed set: written by the renderer and read by pass 3')
    tc, nt = func_src(rp4, 'TC'), func_src(rp4, 'NT')
    ok(S('record\\(', tc) and bool(nt) and not S('record\\(', nt),
       'printed set: recorded from table cells and never from note paragraphs, or a note '
       + 'would launder its own figures into the set and the check would be vacuous')
    ok(S('PRECISION_IS_VERDICT = False', vp4),
       'precision: report-only today, and the promotion is a named constant so a flip has '
       + 'to be a deliberate edit rather than a passing change')
    ok(S('if _dec\\(b\\) >= 2:', vp4),
       'precision: fires only one decimal coarser and only at two decimals or more, which '
       + 'is the measured shape of the defect and not a wider guess')
    ok(S('\\(\\?:%\\|x\\)\\?', vp4),
       'precision: the scanner keeps a trailing x, or a multiple in a note has no unit to '
       + 'compare and the one defect this exists for is invisible')
    sk9 = rd(P(HERE, '..', 'SKILL.md'))
    ok(S('requested PLUS a market code', sk9),
       'non-US: the Step 1 ticker guard tolerates an exchange suffix, or it fails on EVERY '
       + 'non-US ticker and the company-name fallback stops being an exception')
    ok(S('must be the\\s+company you meant', sk9),
       'non-US: and the company-name half of the guard survives the tolerance, because that '
       + 'is the half that caught a request returning a different company')
    dc = rd(P(HERE, 'data-collection.md'))
    ok(S('The benchmark is a DECISION, not a constant', dc) and S('DISCLOSED AS A PROXY', dc),
       'non-US: section 9 resolves a LOCAL benchmark and falls back to the US market index only as a '
       + 'disclosed proxy (a Hong Kong name measured against a US tracker answers the wrong '
       + 'question while looking right)')
    ok(S('the periodic filing the issuer actually', dc),
       'non-US: the document ladder asks for the filing the issuer actually files rather '
       + 'than a 10-Q, which no foreign filer has')
    ok(not S('earnings release and 10-Q at minimum', dc),
       'non-US: and the old US-only phrasing is gone, not merely supplemented')
    ok(S("corp = G\\(C, 'corp'\\)", rp4) and S("cr = jor\\(G\\(corp, 'cells'\\) if jt\\(corp\\) else corp, \\{\\}\\)", rp4),
       'corp: the renderer treats an absent corporate-and-eliminations block as a zero one, '
       + 'so a segment set that reconciles exactly can still build')
    ok(not S("'mix'", rp4) and not S('"mix"', bc4),
       'mix: the dead field is gone from the builder as well as the renderer, so the '
       + 'contract cannot promise a column the page never shows')
    ok(S('growth:<code>:<period>', pcm4),
       'tiles: the contract states the growth accessor, so a Y/Y tile is resolved rather '
       + 'than typed (it said the opposite for three releases, against rule 1)')
    if FX:
        ok(S('normMult', cc4) and S('substring accident', cc4),
           'rule 5: the lens match is on a normalised whole label, not a substring of a joined '
           + 'string, which reported both failure directions at once on the first non-US packet')
    ok(S('R17.*lead', cp4) and not S('g\\("peg"\\), g\\("pe_ntm"\\)', cp4),
       'R17: the PEG audit divides by the LEAD multiple, resolved from val_metrics[].lead '
       + 'the way R16 has since 7.3.0, not by a hardcoded pe_ntm')
    cfg_src = rd(P(HERE, 'check_contract.py'))
    ok(S('AMBIG = \\{', cfg_src) and S("'RMB'", cfg_src),
       'currency: an ambiguous symbol resolves from the label rather than being decided '
       + 'here, and RMB is a currency the gate knows')
    ok(S('CCY_TOK_SRC', cfg_src) and len(re.findall('CCY_TOK_SRC', cfg_src)) >= 3,
       'currency: ONE token list serves both the leading-token test and the readability '
       + 'note, so a code added for one is added for the other')
    ok(S("could NOT be\\s*\\+?\\s*'?cross-checked", cfg_src) or S('cross-checked', cfg_src),
       'currency: an unrecognised currency is a NAMED note, not a silent pass')
    ok(S('val_excluded` lists MULTIPLES, not adjustment bases', pcm4),
       'val_excluded: the contract says it lists multiples outside the lens, not the GAAP '
       + 'sibling of one the lens shows, which is a distinction the reader cannot act on')
    ok(S('lens: every multiple sector_config\\.json gives', cc4),
       "lens: the packet's own lens is cross-checked against the sector key it declares, "
       + 'which is what two shipped sector fixtures were contradicting')
    ok(S('the printed set is absent, so the precision check did NOT run', vp4),
       'precision: an absent printed set is reported at the verdict, so the check cannot '
       + 'no-op behind a green tick')
    ok(S('printed set absent', vp4),
       'precision: under --strict-precision an absent printed set FAILS rather than running '
       + 'blind, the same way an absent figure set does')
    sk7 = rd(P(HERE, '..', 'SKILL.md'))
    ok(S('printed_figures\\.json', pcm4) and S('printed_figures\\.json', sk7),
       'precision: the printed set is documented as a pipeline artifact in the contract AND '
       + 'in the run sequence, so an operator knows what writes it and when')
    if FX:
        ok(os.path.exists(P(HERE, 'fixtures', 'test_precision_gate.py')),
           'precision: both edges of the rule are held by a mutation test, the two shapes it '
           + 'must catch and the six it must not')
    ok(S("NO_EBIT_KEYS = \\['banking', 'financials_nonbank', 'insurance', 'reit'\\]", cc4),
       'B12: the four keys with no EBIT are named in the gate, not inferred')
    ok(S('no tile label may say EBIT', cc4),
       "B12: a tile printing one measure under another measure's label cannot ship, which "
       + 'is the failure that is not visibly wrong')
    ok(S('name the SAME measure', cc4),
       'B12: the profit level and its Y/Y must take the same rung, or the pair is two '
       + 'measures read as one')
    ok(S('profit substitution is recorded', cc4), 'B12: the substitution has to be disclosed in warnings')
    ok(S('B12\\. The row 1 profit tile, where the sector has no EBIT', sc3)
       and S('One scripted exception, and it is narrow', ps3),
       'B12: the ladder is written in sector-config AND reconciled in packet-sections with '
       + 'the do-not-substitute rule it is an exception to')
    if FX:
        ok(S('A spine sub-row is not always a DERIVED margin', bc4),
           '--verify: a sub-row whose denominator cannot be inferred is carried as a fetched row, '
           + 'so the round-trip gate stays armed on banking, insurance, REITs and utilities')

    sk = rd(P(HERE, '..', 'SKILL.md'))
    rp3 = rd(P(HERE, 'render_packet.py'))
    bc3 = rd(P(HERE, 'build_computed.py'))
    cc3 = rd(P(HERE, 'check_contract.py'))
    mf3 = rd(P(HERE, 'fixtures', 'make_fixtures.py')) if FX else ''
    ok(S("segs, ebit = G\\(C, 'segments'\\), G\\(C, 'seg_ebit'\\)", rp3)
       and S('segsOK = isinstance\\(segs, list\\)', rp3) and S('ebitOK = isinstance\\(ebit, list\\)', rp3)
       and S("if ebitOK:\\s*\\n\\s*segBand\\(G\\(C, 'seg_profit_label'\\)\\)", rp3),
       'B6: each segment band is gated on a non-empty array, so a label never prints alone')
    ok(S('bandNote = ', rp3) and S('band is not shown', rp3), 'B6: a suppressed band is disclosed in the note, per rule 7')
    ok(S("js_eq\\(G\\(M, 'reaction_basis'\\), 'after_close'\\)", rp3) and S("RB2\\['pre'\\], RB2\\['day'\\]", rp3),
       'B7: the section 9 price headers derive from meta.reaction_basis')
    ok(S("REACTION_BASIS = \\['intraday', 'after_close'\\]", cc3),
       'B7: check_contract validates a supplied reaction_basis rather than accepting a typo')
    ok(S('def build_peers\\(plan, raw\\)', bc3) and S('DERIVED_PEER = ', bc3),
       'B8: peers.json has a builder, and it names the fields it derives')
    ok(S('which this builder DERIVES', bc3), 'B8: a ratio in the raw peers block is REFUSED, not preferred over the derivation')
    ok(S('def explode_peers\\(PR\\)', bc3) and S('plan\\["peers"\\], raw\\["peers"\\] = explode_peers', bc3),
       'B8: peers round-trips through explode, so --verify covers section 8')
    ok(S('def build_reaction_rows\\(P, R\\)', bc3)
       and S('e\\["m1"\\] = r4\\(pct_change\\(d1, pre\\)\\)', bc3)
       and S('e\\["r0"\\] = r4\\(e\\["m0"\\] - pct_change\\(sday, spre\\)\\)', bc3),
       'B8: r0, m1, m5 and r5 are derived from closes, not read as raw inputs')
    ok(S('and cannot be derived\\. Supply the ', bc3),
       'B8: a half-supplied reaction row refuses by name rather than rendering a dash')
    ok(S('elif kind\\[0\\] == "growth":', bc3), 'B8: the tile from-spec has a growth accessor, so a Y/Y tile need not be typed')
    if FX:
        ok(os.path.exists(P(HERE, 'fixtures', 'test_reaction_closes.py')),
           'B8: the reaction closes path has an acceptance test with hand-derived expectations')
    ok(S('test_reaction_closes\\.py', sk),
       'B8: SKILL.md invokes test_reaction_closes.py (a gate nothing calls is a gate nobody runs)')
    sc3 = rd(P(HERE, 'sector-config.md'))
    cfg = parse(P(HERE, 'sector_config.json'))
    parts = sc3.split('## 3. Valuation lens')
    sec = parts[1] if len(parts) > 1 else UNDEF
    ok(sec is not UNDEF, 'sector config: sector-config.md still carries the lens section the '
       + 'gate reads (a moved heading silently disarms this check)')
    table = (sec if sec is not UNDEF else '').split('### B10')[0]
    doc = {}
    for line in table.split('\n'):
        if not line.startswith('| `'):
            continue
        cols = [x for x in (jtrim(c) for c in line.split('|')) if x]
        if len(cols) < 2:
            continue
        keys = [m.group(1) for m in re.finditer('`([A-Za-z0-9_]+)`', cols[0])]
        mult = jtrim(re.sub('\\s*\\([^)]*\\)\\s*\\Z', '', cols[1], count=1))
        for k in keys:
            doc[k] = mult
    if nn(cfg):
        raise type_error('Cannot convert undefined or null to object')
    ckeys = js_keys(cfg) if isinstance(cfg, dict) else []
    missing = [k for k in ckeys if k not in doc]
    ok(len(missing) == 0, 'sector config: every key in sector_config.json appears in the '
       + 'sector-config.md lens table' + (', absent: ' + ', '.join(missing) if missing else ''))
    bad = []
    for k in ckeys:
        if k not in doc:
            continue
        jv = js_join(need(jor(G(G(cfg, k), 'lens'), []), 'join', '(cfg[k].lens || []).join'), ', ')
        if jv != doc[k]:
            bad.append(k + ': json [' + jv + '] vs md [' + doc[k] + ']')
    ok(len(bad) == 0, 'sector config: the JSON lens and the documented lens are the same '
       + 'multiples in the same order for all ' + str(len(ckeys)) + ' keys'
       + ('; DIFFER on ' + ' | '.join(bad) if bad else ''))
    ok(not S('P/FFO ahead of P/E', table),
       'sector config: the unreachable P/FFO conditional stays out of the reit LENS ROW '
       + '(the feed carries no FFO metric; rule 5 of that section explains why, and may '
       + 'quote the removed line as history)')
    ok(S('P/FFO ahead of P/E', sc3),
       'sector config: and the removed conditional is still recorded in the prose, so the '
       + 'next reader learns why a REIT leads on P/E instead of re-deriving it')
    ok(S('### B9\\.', sc3) and S('the code that satisfied the probe', sc3),
       'B9: sector-config carries the recurring-revenue substitution ladder')
    ok(S('### B10\\.', sc3) and S('EV/Gross profit \\(Adj\\)', sc3), 'B10: sector-config carries the pre-profit valuation ladder')
    ok(S('sector-config\\.md` section B9', sk) and S('section B10', sk),
       'B9/B10: SKILL.md Step 3 points at both ladders rather than leaving them to judgement')
    if FX:
        tr = rd(P(HERE, 'fixtures', 'test_roundtrip.py'))
        ok(S('if not os\\.path\\.isdir\\(FX\\)', tr), 'B11: test_roundtrip reports a missing fixture directory rather than raising')
    ok(S("Earnings Release', 'gi'\\), s, '\\$1 REL'", rp3),
       'B11: a release name is abbreviated the way a transcript is, so it cannot wrap')
    if FX:
        ok(S('no-segment-profit', mf3) and S('after-close', mf3),
           'B6/B7: the two fixtures whose page hash is the regression signal both ship')
    vrs = rd(P(HERE, 'validate_rules_style.py'))
    ok(S('def _is_orange\\(c\\)', vrs) and S('if _is_orange\\(d\\.get\\("color"\\)\\):', vrs),
       'rules-style: the orange heading rule is excluded from table detection, so a heading '
       + 'at a page top is not a one-row fragment')

    sk = rd(P(HERE, '..', 'SKILL.md'))
    fmt = rd(P(HERE, 'fmt.py'))
    rp2 = rd(P(HERE, 'render_packet.py'))
    bc2 = rd(P(HERE, 'build_computed.py'))
    cp2 = rd(P(HERE, 'compute.py'))
    cc2 = rd(P(HERE, 'check_contract.py'))
    ok(S("^SCALE = 'B'$", fmt, re.M) and S('def setScale', fmt) and S('must be "B" or "M"', fmt),
       'B1: fmt.py carries the money scale as a setting and setScale throws on an unknown value')
    ok(S("v if SCALE == 'M' else to_num\\(v\\) / 1000", fmt),
       "B1: the '$M' case reads the scale rather than dividing by 1000 unconditionally")
    ok(len(re.findall('setScale\\(', rp2)) == 1 and S("setScale\\(G\\(M, 'money_scale'\\)\\)", rp2),
       'B1: the renderer arms the scale once, from meta, beside the currency symbol')
    ok(S("cl = G\\(M, 'ccy_label'\\)", rp2) and S('scaleLabel\\(\\) if nn\\(cl\\) else cl', rp2),
       'B1: the masthead Figures field derives from the scale, so it cannot assert the wrong one')
    ok(S('money_scale', bc2) and S('fmt_value\\(v, f, ccy, money_scale\\)', bc2),
       'B1: a tile derived through a `from` spec formats at the packet scale')
    ok(S("MONEY_SCALE = \\['B', 'M'\\]", cc2) and S('money_scale is absent', cc2),
       'B1: check_contract validates a supplied scale and notes an absent one on a small-cap spine')
    ok(S('no two values in one row print the same string at money_scale', cc2),
       'B1: check_contract FAILS a collapsed money column, the symptom no other gate can see')
    ok(S('def pct_change\\(cur, base\\)', bc2) and S('def pct_change\\(cur, base\\)', cp2),
       'B2: builder and audit each hold their own pct_change, so the audit imports nothing')
    ok(S('\\(cur - base\\) / abs\\(base\\) \\* 100', bc2) and S('\\(cur - base\\) / abs\\(base\\) \\* 100', cp2),
       'B2: pct_change divides by the ABSOLUTE base, so a widening loss is negative')
    ok(not S('\\(a / b - 1\\) \\* 100', bc2) and not S('\\(b / a - 1\\) \\* 100', bc2)
       and not S('\\(mean / prior - 1\\) \\* 100', bc2) and not S('\\(ltm / base - 1\\) \\* 100', bc2),
       'B2: no growth, dispersion, revision or valuation comparison still uses current/base - 1')
    ok(not S('\\(a / b - 1\\) \\* 100', cp2) and not S('\\(hi / lo - 1\\) \\* 100', cp2)
       and not S('\\(latest / pre - 1\\) \\* 100', cp2),
       'B2: R3, R4, R5, R8 and R14 to R16 re-derive the same identity the builder uses')
    pcm = rd(P(HERE, 'pipeline-contracts.md'))
    ok(S('\\(current - base\\) / \\|base\\| \\* 100', sk) and S('^11\\. \\*\\*Every percentage change', sk, re.M)
       and S('SKILL\\.md rule 11', pcm),
       'B2: SKILL.md states the percentage-change identity as non-negotiable rule 11, and '
       + 'the contract cross-references it')
    pfm = re.search('PF = \\{[\\s\\S]*?\\n        \\}', rp2)
    pf = pfm.group(0) if pfm else ''
    names = ['money', 'mult', 'pct1', 'pct100', 'pctchg', 'ratio', 'raw']
    missing = [n for n in names if not S("'" + n + "'\\s*:[\\s\\S]*?'c': (DK|GN) if js_ge\\(v, 0\\) else RD", pf)]
    ok(len(missing) == 0, 'B3: every peer format colours on sign' + (', missing: ' + ', '.join(missing) if missing else ''))
    ok(S("'pctchg':[^\\n]*'c': (GY if gzero\\(v, False\\) else \\()?GN if js_ge\\(v, 0\\) else RD", pf)
       and all(not S("'" + n + "'\\s*:[^\\n]*'c': GN if js_ge\\(v, 0\\) else RD", pf) for n in names if n != 'pctchg'),
       'B3: only pctchg takes the green branch; a level is ink when positive, never green')
    ok(S("'raw'\\s*:[\\s\\S]*?num\\(v, 2\\)", pf) and S('is_num\\(v\\)', pf),
       'B3: `raw` formats a number through num() and passes a string through in ink')
    ok(S('every tile printing a negative number carries `sign`', cc2),
       'B5: check_contract fails a negative tile that carries no sign')
    ok(S('`sign` is REQUIRED on any tile whose value prints a negative number', rd(P(HERE, 'pipeline-contracts.md'))),
       'B5: the contract states the tile-sign requirement rather than calling sign optional')
    if FX:
        mf = rd(P(HERE, 'fixtures', 'make_fixtures.py'))
        ok(S('loss-making', mf) and S('small-cap', mf), 'B4: make_fixtures.py ships the loss-making and small-cap fixtures')

    inputs = ['packet_computed.json', 'packet_narrative.json', 'peers.json']
    if all(os.path.exists(f) for f in inputs):
        d = tempfile.mkdtemp(prefix='ep3p-det-')

        def rdx(p):
            with zipfile.ZipFile(p) as z:
                return hashlib.sha256(z.read('word/document.xml')).hexdigest()
        try:
            a, b = os.path.join(d, 'a.docx'), os.path.join(d, 'b.docx')
            for x in (a, b):
                cmd = [sys.executable, os.path.join(HERE, 'render_packet.py'), x]
                if subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL).returncode != 0:
                    raise RuntimeError('Command failed: ' + ' '.join(cmd))
            ok(rdx(a) == rdx(b), 'two renders of the same inputs produce the same page (word/document.xml)')
        except Exception as e:
            ok(False, 'determinism check could not run: ' + str(e))
        finally:
            shutil.rmtree(d, ignore_errors=True)
    else:
        out('  skip  determinism - run from a packet working directory to test it')

    out('\nSELF CHECK ' + ('FAILED, ' + str(st['fail']) + ' problem(s)' if st['fail'] else 'PASSED')
        + ('' if VERBOSE else ' (%d checks passed, not listed)' % st.get('ok', 0)))
    return 1 if st['fail'] else 0


def _uncaught(e):
    if isinstance(e, JSError):
        head = '%s: %s' % (e.name, e.message)
    else:
        head = '%s: %s' % (type(e).__name__, e)
    tb = ''.join('    at ' + ln.strip() + '\n' for ln in traceback.format_tb(e.__traceback__) if ln.strip())
    sys.stdout.flush()
    sys.stderr.buffer.write(_utf8(os.path.join(HERE, 'self_check.py') + '\n\n' + head + '\n' + tb))
    sys.stderr.flush()
    sys.exit(1)


if __name__ == '__main__':
    try:
        rc = main(sys.argv[1:])
    except SystemExit:
        raise
    except BaseException as _e:
        _uncaught(_e)
    sys.stdout.flush()
    sys.exit(rc)
