#!/usr/bin/env python3
"""check_table_widths.py v3 - the geometry regression gate.

    python3 check_table_widths.py <file.docx> [...more.docx]

Python standard library only; the twin of check_table_widths.js, which it replaced in 8.0.0
(that file also needed an `unzip` binary on PATH; this one reads the .docx itself).

TWO defect classes, both of which shipped past earlier checks:

  (A) OVERFLOW. A table wider than its container. A rescale helper once forced every authored
      width array to sum to the text column, which is wrong for the half-width tables nested
      inside a two-up block: the two halves rendered on top of each other. Page-level ink
      measurement cannot see this - the overflow is clipped at the page edge.

  (B) GRID vs CELLS. <w:tblGrid> says one width and the row's <w:tcW> say another. LibreOffice
      silently reconciles it; Word is not guaranteed to. v1 compared the grid to its CONTAINER
      only, so it passed a build with ten mismatched tables.

Handles nesting (depth-tracked) and w:gridSpan (a spanned cell covers several grid columns).

Exit 0 = sound. Exit 1 = at least one table disagrees with its container or itself.
Exit 2 = no file given.
"""
import os
import re
import sys
import zipfile

TOL = 12

_TOK = re.compile(r'<w:tbl>|</w:tbl>|<w:tr>|</w:tr>|<w:tc>|</w:tc>|<w:tcW[^>]*>|<w:tblW[^>]*>'
                  r'|<w:gridCol[^>]*>|<w:gridSpan[^>]*>|<w:tblGrid>|</w:tblGrid>')


def attr(tag, name):
    m = re.search(re.escape(name) + r'="(\d+)"', tag, re.ASCII)
    return int(m.group(1)) if m else None


def document_xml(docx):
    with zipfile.ZipFile(docx) as z:
        return z.read('word/document.xml').decode('utf-8', 'replace')


def text_column(xml):
    sz = re.search(r'<w:pgSz[^>]*>', xml)
    mar = re.search(r'<w:pgMar[^>]*>', xml)
    if not sz or not mar:
        return None
    w, l, r = attr(sz.group(0), 'w:w'), attr(mar.group(0), 'w:left'), attr(mar.group(0), 'w:right')
    return None if None in (w, l, r) else w - l - r


def audit(xml, column):
    tbls, stack, cell_stack = [], [], []
    idx, row = 0, None
    for t in _TOK.findall(xml):
        if t == '<w:tbl>':
            idx += 1
            stack.append({'index': idx, 'depth': len(stack),
                          'container': column if not stack else (cell_stack[-1] if cell_stack else None),
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
            if stack and row and row['cells']:
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
        sys.stderr.write('usage: python3 check_table_widths.py <file.docx> [...]\n')
        return 2
    failed = 0
    for f in files:
        if not os.path.exists(f):
            sys.stderr.write('MISSING  %s\n' % f)
            failed += 1
            continue
        try:
            xml = document_xml(f)
        except (OSError, KeyError, zipfile.BadZipFile) as e:
            sys.stderr.write('UNREADABLE  %s: %s\n' % (f, e))
            failed += 1
            continue
        column = text_column(xml)
        if not column:
            sys.stderr.write('NO SECTPR  %s\n' % f)
            failed += 1
            continue
        tbls = audit(xml, column)
        problems = []
        for t in tbls:
            if not t['grid']:
                if t['rows']:
                    problems.append('  NO GRID   table #%d (depth %d): %d row(s) but no <w:gridCol> widths '
                                    '(the <w:tblGrid> is absent or empty), so column widths are undefined '
                                    'and cannot be checked. The renderer must emit a grid with one gridCol '
                                    'per column for every table.' % (t['index'], t['depth'], len(t['rows'])))
                continue
            gsum = sum(t['grid'])
            what = 'text column' if t['depth'] == 0 else 'parent cell'
            if t['container'] is not None and gsum - t['container'] > TOL:
                problems.append('  OVERFLOW  table #%d (depth %d): grid %d exceeds %s %d by %d'
                                % (t['index'], t['depth'], gsum, what, t['container'], gsum - t['container']))
            elif t['depth'] == 0 and t['container'] is not None and t['container'] - gsum > TOL:
                problems.append('  SHORT     table #%d: grid %d is %d narrower than the text column '
                                '— breaks the shared right edge' % (t['index'], gsum, t['container'] - gsum))
            if t['tblW'] is not None and abs(t['tblW'] - gsum) > TOL:
                problems.append('  DECLARED  table #%d: w:tblW %d != grid %d — two widths for one table'
                                % (t['index'], t['tblW'], gsum))
            for r in t['rows']:
                if r['cells'] + r['span'] != len(t['grid']):
                    continue
                if abs(r['sum'] - gsum) > TOL:
                    problems.append('  CELLS     table #%d: a row\'s w:tcW sum %d != grid %d (diff %d) '
                                    '— grid rescaled without its cells'
                                    % (t['index'], r['sum'], gsum, r['sum'] - gsum))
                    break
        nested = sum(1 for t in tbls if t['depth'] > 0)
        if problems:
            print('FAIL  %s\n      column %d dxa | %d tables (%d nested)' % (f, column, len(tbls), nested))
            for pr in problems:
                print(pr)
            failed += 1
        else:
            print('ok    %s — column %d dxa, %d tables (%d nested), grid+cells sound'
                  % (f, column, len(tbls), nested))
    if failed:
        sys.stderr.write('\n%d file(s) failed the table-width invariant.\n' % failed)
        return 1
    print('\nTABLE WIDTH INVARIANT PASSED')
    return 0


if __name__ == '__main__':
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding='utf-8', newline='\n')
        except (AttributeError, ValueError):
            pass
    sys.exit(main(sys.argv[1:]))
