"""
Restated fiscal-period labels: grid resolution and cell keying.

An issuer that re-presented its historicals after a spin-off or a segment change carries a
`R-` prefix on the affected FISCALPERIOD labels. Two consequences, both visible in the raw
feed and both silent if ignored:

  1. `search_tickerid` stops at the last period BEFORE the restated band, so the pointer
     goes stale: the pointer can read 4Q22 while actuals run to 2Q26.

  2. `get_actuals` echoes the restated label, not the label requested. A request for 2Q25
     comes back as FISCALPERIOD "R-2Q25". The VALUE is correct. Keying a cell on the
     returned label puts it in a bucket nothing reads, so the cell disappears with no error.

The restated band usually spans a full fiscal year, which is why the loss is doubled: it
takes out history columns AND the prior-year bases of the quarters after it, emptying both
a level and a growth row on the same line item. At a 3Q26 target that can be 12 of 16
cells across two line items, and nothing on the page looks missing.

Run this file directly to execute the restatement fixture.
"""

import re

R_PREFIX = re.compile(r'^R\d*-')


def norm(period):
    return R_PREFIX.sub('', str(period).strip())


def is_restated(period):
    return bool(R_PREFIX.match(str(period).strip())) if period is not None else False


def index_actuals(rows):
    cells, restated, seen_clean, both = {}, set(), set(), []
    for r in rows:
        p_raw = r['FISCALPERIOD']
        p     = norm(p_raw)
        code  = r['LTCODE']
        key   = (code, p)
        d     = cells.setdefault(code, {})
        if is_restated(p_raw):
            restated.add(p)
            if key in seen_clean:
                both.append(key)
                continue
            d[p] = r['VALUE']
        else:
            if p in d and key not in seen_clean:
                both.append(key)
            seen_clean.add(key)
            d[p] = r['VALUE']
    return cells, sorted(restated), sorted(set(both))


def resolve_grid(calendar, has_actuals, today, pointer=None, target=None,
                 periodicity=None):
    def axis_rank(p):
        half = bool(re.fullmatch(r'[12]H\d{2}', str(p)))
        quarter = bool(re.fullmatch(r'[1-4]Q\d{2}', str(p)))
        annual = bool(re.fullmatch(r'\d{4}', str(p)))
        if periodicity == 'semi_annual':
            return 0 if half else (1 if quarter else 2)
        if periodicity == 'annual':
            return 0 if annual else (1 if half else 2)
        if periodicity == 'quarterly':
            return 0 if quarter else (1 if half else 2)
        return 0 if quarter else (1 if half else 2)

    dated = [(p, d) for p, d in calendar.items() if d]
    ties = sorted({d for p, d in dated if sum(1 for q, e in dated if e == d) > 1})

    _seen = {}

    def reported(p):
        if p not in _seen:
            _seen[p] = bool(has_actuals(p))
        return _seen[p]

    def is_past(p):
        return calendar[p] < today or (calendar[p] == today and reported(p))

    def is_future(p):
        return calendar[p] > today or (calendar[p] == today and not reported(p))

    past = sorted([p for p, d in dated if is_past(p)],
                  key=lambda p: (axis_rank(p), str(p)))
    past.sort(key=lambda p: calendar[p], reverse=True)
    future = sorted([p for p, d in dated if is_future(p)],
                    key=lambda p: (axis_rank(p), str(p)))
    future.sort(key=lambda p: calendar[p])

    last = next((p for p in past if reported(p)), None)
    if last is None:
        raise ValueError('no period with a passed call date returns actuals; '
                         'widen the get_call_date window')
    unloaded = [p for p in past if not reported(p) and calendar[p] > calendar[last]]

    if target is not None:
        if target not in calendar:
            raise ValueError(
                'requested target %r is not in the call-date calendar; widen the '
                'get_call_date window or check the label' % (target,))
        if target not in future:
            raise ValueError(
                'requested target %r has a call date of %s, which is not in the future: '
                'it has already reported. Use earnings-wrap.'
                % (target, calendar[target]))
    else:
        if not future:
            raise ValueError(
                'the call-date calendar carries no unreported period after %s, so there '
                'is no target to preview. Widen the get_call_date window; the next '
                'fiscal year is usually published later than the current one.' % today)
        target = future[0]

    if target == last:
        raise ValueError(
            'target %r is also the last reported period (call date %s): it has already '
            'reported. This skill is pre-earnings only, use earnings-wrap.'
            % (target, calendar.get(target)))

    lp, cd = calendar.get(last), calendar.get(target)
    return dict(
        pointer=pointer,
        pointer_was_stale=(pointer is not None and pointer != last),
        last_reported=last,
        target=target,
        last_print_date=lp,
        call_date=cd,
        reported_not_loaded=unloaded,
        last_print_is_real=(lp is not None and lp <= today),
        target_is_unreported=(cd is not None and (cd > today or not reported(target))),
        ties=ties,
    )


def check_yoy(derived, feed_yoy, tol=0.02):
    if derived is None:
        return ('LOST', None)
    if feed_yoy is None:
        return ('NO FEED VALUE', None)
    return ('OK' if abs(derived - feed_yoy) <= tol else 'MISMATCH', derived - feed_yoy)



_FIX_ROWS = [
    ("IS_P_101", "R-3Q25",    5900.0,  18.00), ("IS_P_101", "R-4Q25",    6200.0,  14.81),
    ("IS_P_101", "1Q26",      6000.0,  17.65), ("IS_P_101", "2Q26",      6500.0,  18.18),
    ("IS_P_101", "3Q24",      5000.0,   4.10), ("IS_P_101", "4Q24",      5400.0,   5.20),
    ("IS_P_101", "R-1Q25",    5100.0,   6.30), ("IS_P_101", "R-2Q25",    5500.0,   7.40),
    ("IS_P_128", "R-3Q25",       1.0,  25.00), ("IS_P_128", "R-4Q25",      1.05,  16.67),
    ("IS_P_128", "1Q26",        1.02,  20.00), ("IS_P_128", "2Q26",         1.1,  15.79),
    ("IS_P_128", "3Q24",         0.8,   5.10), ("IS_P_128", "4Q24",         0.9,   6.20),
    ("IS_P_128", "R-1Q25",      0.85,   7.30), ("IS_P_128", "R-2Q25",      0.95,   8.40),
]
_FIX_ANNUAL = [("IS_P_101", "R1-2025", 22700.0), ("IS_P_128", "R1-2025", 3.85)]
_FIX_CALL = {"1Q26": "2026-04-21", "2Q26": "2026-07-16", "3Q26": "2026-10-21"}
_FIX_BASE = {"3Q25": "3Q24", "4Q25": "4Q24", "1Q26": "1Q25", "2Q26": "2Q25"}


def _selftest(today="2026-08-21"):
    rows = [dict(LTCODE=c, FISCALPERIOD=p, VALUE=v) for c, p, v, _ in _FIX_ROWS]
    feed = {(c, norm(p)): y for c, p, v, y in _FIX_ROWS}
    cells, restated, both = index_actuals(rows)
    fails = []

    for order in (("4Q26", "R-4Q26"), ("R-4Q26", "4Q26")):
        pair = [dict(LTCODE="IS_P_101", FISCALPERIOD=order[0], VALUE=100.0),
                dict(LTCODE="IS_P_101", FISCALPERIOD=order[1], VALUE=999.0)]
        want = 100.0 if not is_restated(order[0]) else 999.0
        c2, _, b2 = index_actuals(pair)
        if c2["IS_P_101"].get("4Q26") != want:
            fails.append("precedence %s -> %r, want %r"
                         % (order, c2["IS_P_101"].get("4Q26"), want))
        if b2 != [("IS_P_101", "4Q26")]:
            fails.append("dual-label pair not reported for order %s: %r" % (order, b2))

    g = resolve_grid(_FIX_CALL, lambda p: p in {norm(r["FISCALPERIOD"]) for r in rows},
                     today, pointer="4Q22")
    for k, want in (("last_reported", "2Q26"), ("target", "3Q26"),
                    ("pointer_was_stale", True), ("last_print_is_real", True),
                    ("target_is_unreported", True)):
        if g[k] != want:
            fails.append("grid.%s = %r, want %r" % (k, g[k], want))
    if restated != ["1Q25", "2Q25", "3Q25", "4Q25"]:
        fails.append("restated set = %r" % (restated,))

    for code in ("IS_P_101", "IS_P_128"):
        for q, base in _FIX_BASE.items():
            new, old = cells[code].get(q), cells[code].get(base)
            d = None if (new is None or not old) else (new / old - 1) * 100.0
            st, _ = check_yoy(d, feed.get((code, q)))
            if st != "OK":
                fails.append("%s %s Y/Y %s" % (code, q, st))

    raw = {}
    for c, p, v, _ in _FIX_ROWS:
        raw.setdefault(c, {})[p] = v
    losses = sum(1 for c in ("IS_P_101", "IS_P_128") for q in _FIX_BASE
                 if raw[c].get(q) is None or raw[c].get(_FIX_BASE[q]) is None)
    if losses != 8:
        fails.append("raw-label keying lost %d of 8 growth cells, expected 8" % losses)

    for code, lab, val in _FIX_ANNUAL:
        if norm(lab) != "2025":
            fails.append("annual label %s normalised to %r, want '2025'" % (lab, norm(lab)))
        if not is_restated(lab):
            fails.append("annual label %s not flagged as restated" % lab)

    for f in fails:
        print("  FAIL  " + f)
    print("PASS  restatement fixture: pointer 4Q22 resolves to last_reported 2Q26, target 3Q26; "
          "4 restated periods normalised; 16 of 16 cells present; every Y/Y matches the "
          "feed" if not fails else "FAILED, %d problem(s)" % len(fails))
    return not fails


if __name__ == "__main__":
    import sys
    sys.exit(0 if _selftest() else 1)
