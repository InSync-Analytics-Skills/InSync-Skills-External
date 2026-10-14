#!/usr/bin/env python3
"""check_table_widths.py: the geometry regression gate.

    python3 check_table_widths.py <file.docx> [...more.docx]

Python standard library only. It reads the .docx with zipfile, so it needs no unzip binary.
Where the archive cannot be read its message quotes unzip's own words, for the three shapes
that occur (not a zip, no word/document.xml, a directory).

The defect classes, none of which an ink-level check can see:

  (A) OVERFLOW. A table wider than its container. A rescale helper that forces every authored
      width array to sum to TW is right for a full-width table and wrong for the
      half-width tables nested inside a two-up block: they inflate to the full column and
      the two halves render on top of each other. Page-level ink measurement
      cannot see this: the overflow is clipped at the page edge.

  (B) GRID vs CELLS. <w:tblGrid> says one width and the row's <w:tcW> say another. LibreOffice
      silently reconciles it; Word is not guaranteed to. Comparing the grid to its CONTAINER
      only misses it, so the rows are compared too.

  (C) SHORT ROW. A row with no gridSpan that carries fewer cells than the grid has
      columns is a builder that lost count; rows that disagree with each other on total width
      are the same defect from the other side (RAGGED).

Handles nesting (depth-tracked) and w:gridSpan (a spanned cell covers several grid cols).

Exit 0 = sound. Exit 1 = at least one table disagrees with its container or itself.
Exit 2 = no file given.
"""
import os
import re
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
from jscompat import js_str, js_json

TOL = 12

_TOK = re.compile(r'<w:tbl>|</w:tbl>|<w:tr>|</w:tr>|<w:tc>|</w:tc>|<w:tcW[^>]*>|<w:tblW[^>]*>'
                  r'|<w:gridCol[^>]*>|<w:gridSpan[^>]*>|<w:tblGrid>|</w:tblGrid>')

_NOT_ZIP = ('  End-of-central-directory signature not found.  Either this file is not\n'
            '  a zipfile, or it constitutes one disk of a multi-part archive.  In the\n'
            '  latter case the central directory and zipfile comment will be found on\n'
            '  the last disk(s) of this archive.\n')


def _w(s):
    sys.stdout.buffer.write(s.encode('utf-8', 'surrogateescape'))


def _e(s):
    sys.stderr.buffer.write(s.encode('utf-8', 'surrogateescape'))


def _num(digits):
    v = int(digits)
    return v if v < 2 ** 53 else float(digits)


def attr(tag, name):
    m = re.search(re.escape(name) + '="([0-9]+)"', tag)
    return _num(m.group(1)) if m else None


class _Unreadable(Exception):

    def __init__(self, message, child_err):
        Exception.__init__(self, message)
        self.message = message
        self.child_err = child_err


def document_xml(docx):
    cmd = 'unzip -p %s word/document.xml' % js_json(docx)
    if os.path.isdir(docx):
        raise _Unreadable('Command failed: %s' % cmd, '')
    try:
        z = zipfile.ZipFile(docx)
    except (zipfile.BadZipFile, OSError):
        err = '[%s]\n%s' % (docx, _NOT_ZIP)
        raise _Unreadable('Command failed: %s\n%s' % (cmd, err), err)
    with z:
        try:
            data = z.read('word/document.xml')
        except KeyError:
            err = 'caution: filename not matched:  word/document.xml\n'
            raise _Unreadable('Command failed: %s\n%s' % (cmd, err), err)
        except Exception as e:
            raise _Unreadable('Command failed: %s\n%s\n' % (cmd, e), '')
    return data.decode('utf-8', 'replace')


def text_column(xml):
    sz = re.search(r'<w:pgSz[^>]*>', xml)
    mar = re.search(r'<w:pgMar[^>]*>', xml)
    if not sz or not mar:
        return None
    w, l, r = attr(sz.group(0), 'w:w'), attr(mar.group(0), 'w:left'), attr(mar.group(0), 'w:right')
    return None if (w is None or l is None or r is None) else w - l - r


def audit(xml, column):
    tbls, stack, cell_stack = [], [], []
    idx, row = 0, None
    for t in _TOK.findall(xml):
        if t == '<w:tbl>':
            idx += 1
            stack.append({'index': idx, 'depth': len(stack),
                          'container': column if not stack else (cell_stack[-1] if cell_stack and cell_stack[-1] else None),
                          'grid': [], 'tblW': None, 'inGrid': False, 'rows': []})
        elif t == '</w:tbl>':
            if stack:
                tbls.append(stack.pop())
        elif t == '<w:tblGrid>':
            if stack:
                stack[-1]['inGrid'] = True
        elif t == '</w:tblGrid>':
            if stack:
                stack[-1]['inGrid'] = False
        elif t.startswith('<w:gridCol'):
            w = attr(t, 'w:w')
            if stack and stack[-1]['inGrid'] and w is not None:
                stack[-1]['grid'].append(w)
        elif t.startswith('<w:tblW'):
            w = attr(t, 'w:w')
            if stack and stack[-1]['tblW'] is None and w is not None:
                stack[-1]['tblW'] = w
        elif t == '<w:tr>':
            row = {'sum': 0, 'cells': 0, 'span': 0}
        elif t == '</w:tr>':
            if stack and row is not None and row['cells']:
                stack[-1]['rows'].append(row)
            row = None
        elif t == '<w:tc>':
            cell_stack.append(None)
        elif t == '</w:tc>':
            if cell_stack:
                cell_stack.pop()
        elif t.startswith('<w:tcW'):
            w = attr(t, 'w:w')
            if cell_stack and w is not None:
                cell_stack[-1] = w
                if row is not None and len(cell_stack) == len(stack):
                    row['sum'] += w
                    row['cells'] += 1
        elif t.startswith('<w:gridSpan'):
            v = attr(t, 'w:val')
            if row is not None and v:
                row['span'] += v - 1
    return tbls


def main(files):
    if not files:
        _e('usage: python3 check_table_widths.py <file.docx> [...]\n')
        return 2
    failed = 0
    for f in files:
        if not os.path.exists(f):
            _e('MISSING  %s\n' % f)
            failed += 1
            continue
        try:
            xml = document_xml(f)
        except _Unreadable as e:
            _e(e.child_err)
            _e('UNREADABLE  %s: %s\n' % (f, e.message))
            failed += 1
            continue
        column = text_column(xml)
        if not column:
            _e('NO SECTPR  %s\n' % f)
            failed += 1
            continue
        tbls = audit(xml, column)
        problems = []
        S = js_str
        for t in tbls:
            if not t['grid']:
                continue
            gsum = sum(t['grid'])
            what = 'text column' if t['depth'] == 0 else 'parent cell'
            if t['container'] is not None and gsum - t['container'] > TOL:
                problems.append('  OVERFLOW  table #%s (depth %s): grid %s exceeds %s %s by %s'
                                % (t['index'], t['depth'], S(gsum), what, S(t['container']), S(gsum - t['container'])))
            elif t['depth'] == 0 and t['container'] is not None and t['container'] - gsum > TOL:
                problems.append('  SHORT     table #%s: grid %s is %s narrower than the text column: breaks the '
                                'shared right edge' % (t['index'], S(gsum), S(t['container'] - gsum)))
            if t['tblW'] is not None and abs(t['tblW'] - gsum) > TOL:
                problems.append('  DECLARED  table #%s: w:tblW %s != grid %s: two widths for one table'
                                % (t['index'], S(t['tblW']), S(gsum)))
            for r in t['rows']:
                if r['cells'] + r['span'] != len(t['grid']):
                    continue
                if abs(r['sum'] - gsum) > TOL:
                    problems.append("  CELLS     table #%s: a row's w:tcW sum %s != grid %s (diff %s): grid rescaled "
                                    'without its cells' % (t['index'], S(r['sum']), S(gsum), S(r['sum'] - gsum)))
                    break
            shapes = {}
            for r in t['rows']:
                k = S(r['cells']) + 'c/' + S(r['sum'])
                shapes[k] = shapes.get(k, 0) + 1
            short_no_span = [r for r in t['rows'] if r['span'] == 0 and r['cells'] < len(t['grid'])]
            if short_no_span:
                s0 = short_no_span[0]
                problems.append('  SHORTROW  table #%s: %d row(s) carry %s cell(s) over a %d-column grid with no '
                                'gridSpan, summing %s against grid %s: the builder lost count. Shapes %s'
                                % (t['index'], len(short_no_span), S(s0['cells']), len(t['grid']), S(s0['sum']),
                                   S(gsum), ' '.join(shapes)))
            elif len(set(r['sum'] for r in t['rows'])) > 1:
                problems.append('  RAGGED    table #%s: its rows disagree on total width. Shapes %s'
                                % (t['index'], ', '.join('%s x%d' % (k, v) for k, v in shapes.items())))
        nested = sum(1 for t in tbls if t['depth'] > 0)
        if problems:
            _w('FAIL  %s\n      column %s dxa | %d tables (%d nested)\n' % (f, S(column), len(tbls), nested))
            for p in problems:
                _w(p + '\n')
            failed += 1
        else:
            _w('ok    %s: column %s dxa, %d tables (%d nested), grid+cells sound\n' % (f, S(column), len(tbls), nested))
    if failed:
        _e('\n%d file(s) failed the table-width invariant.\n' % failed)
        return 1
    _w('\nTABLE WIDTH INVARIANT PASSED\n')
    return 0


if __name__ == '__main__':
    rc = main(sys.argv[1:])
    sys.stdout.flush()
    sys.stderr.flush()
    sys.exit(rc)
