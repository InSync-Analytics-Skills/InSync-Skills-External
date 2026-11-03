"""Pick one forward consensus row per line item and period from an InSync history pull.

Forward periods (not yet reported) must come from get_estimates with
latest:{"enabled":false} and an update-date window. The default "latest" record can be a
one- or two-broker partial update in the days around a print, and the history itself
carries thin rows while the panel rebuilds after a print.

Rule, per (LTCODE, FISCALPERIOD):
  1. Rows without a positive BROKERCOUNT or a readable UPDATEDATE are not usable.
     A line item with no usable row is DROPPED (never stops the build).
  2. full = the deepest panel in the window; a row is adequate when it has at least
     half that many brokers.
  3. The newest adequate row is the consensus.
  4. post_from (earnings-wrap / earnings-review post-print columns only): the newest
     adequate row dated on or after post_from. When none exists, the newest adequate
     row before it is returned with status "not_revised".
"""
import datetime as _dt
import math

MIN_SHARE = 0.5


def _date(v):
    if not v:
        return None
    try:
        return _dt.date.fromisoformat(str(v).strip()[:10])
    except ValueError:
        return None


def _brokers(v):
    try:
        n = int(v)
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def _field(row, name):
    if name in row:
        return row[name]
    return row.get(name.lower())


def _period(row):
    return str(_field(row, "FISCALPERIOD") or _field(row, "CALENDARIZED_FISCALPERIOD") or "")


def pick_forward_consensus(rows, post_from=None, min_share=MIN_SHARE):
    cutoff = _date(post_from) if post_from else None
    groups = {}
    order = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        key = (str(_field(row, "LTCODE") or ""), _period(row))
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(row)

    picked, dropped = {}, []
    for key in order:
        usable = []
        for row in groups[key]:
            d, n = _date(_field(row, "UPDATEDATE")), _brokers(_field(row, "BROKERCOUNT"))
            if d is not None and n is not None:
                usable.append((d, n, row))
        if not usable:
            dropped.append((key[0], key[1], "no broker count or update date on any consensus row"))
            continue
        full = max(n for _, n, _ in usable)
        floor = max(1, math.ceil(full * min_share))
        adequate = sorted((u for u in usable if u[1] >= floor), key=lambda u: (u[0], u[1]))
        status = "current"
        if cutoff is not None:
            post = [u for u in adequate if u[0] >= cutoff]
            if post:
                adequate, status = post, "post_print"
            else:
                status = "not_revised"
        d, n, row = adequate[-1]
        picked[key] = {"row": row, "status": status, "brokers": n, "full_panel": full,
                       "asof": d.isoformat()}
    return picked, dropped


def drop_notes(dropped):
    return ["[forward consensus dropped] %s %s: %s, so the line item was removed."
            % (lt, per, why) for lt, per, why in dropped]
