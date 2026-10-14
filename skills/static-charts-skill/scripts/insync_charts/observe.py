"""The computed Key Observation line.

One line under every chart stating what the plotted numbers *are* — latest value,
change, spread, rank, coverage. Computed here in Python from the same values the
chart draws.

This is deliberately **not** narrative and **not** a conclusion. "EBIT margin
46.3% in 3Q26, +3.2pp vs 4Q24" is a fact the reader can check against the chart.
"Margins are expanding strongly" is an interpretation, and interpretation belongs
to the calling skill, which has the context to make it. Keeping the line
computed also keeps it deterministic: the same spec yields the same sentence.

A caller that wants its own wording sets `options.observation` to a string, or
`false` to suppress the line entirely.
"""
from __future__ import annotations

from typing import List, Optional, Sequence

from . import fmt

Number = Optional[float]


def _last(values: Sequence[Number]):
    for i in range(len(values) - 1, -1, -1):
        if values[i] is not None:
            return i, values[i]
    return None, None


def _first(values: Sequence[Number]):
    for i, v in enumerate(values):
        if v is not None:
            return i, v
    return None, None


def _label(periods: Sequence[str], i: Optional[int]) -> str:
    if i is None or not periods or i >= len(periods):
        return ""
    return str(periods[i])


def _pct_change(a: Number, b: Number) -> Number:
    if a is None or b in (None, 0):
        return None
    if a <= 0 or b <= 0:
        return None
    return (a / b - 1.0) * 100.0


def _pp(unit: Optional[str]) -> bool:
    return (unit or "") in ("pct", "pct_pt")


EXACT_UNITS = {"pct", "pct_pt", "x", "usd_ps", "bps", "days"} | {u for u in fmt.UNITS if u.endswith("_ps")}


def _v(value: Number, unit: Optional[str]) -> str:
    return fmt.fmt_value(value, unit, compact=(unit or "") not in EXACT_UNITS)


def _ordinal(n: int) -> str:
    if 10 <= n % 100 <= 20:
        return f"{n}th"
    return f"{n}{ {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th') }"


def _coverage(values: Sequence[Number]) -> str:
    n = len(values)
    got = sum(1 for v in values if v is not None)
    return "" if got == n else f"{got} of {n} periods reported"


def _join(parts: List[str]) -> str:
    return "; ".join([p for p in parts if p])


def _percentile_of(value: float, values: Sequence[Number]) -> Optional[float]:
    xs = sorted(v for v in values if v is not None)
    if len(xs) < 3:
        return None
    below = sum(1 for x in xs if x < value)
    return below / (len(xs) - 1) * 100.0 if len(xs) > 1 else None


def for_chart(spec, primitive: str) -> str:
    override = spec.opt("observation", None)
    if override is False:
        return ""
    if isinstance(override, str) and override.strip():
        return override.strip()

    periods = spec.periods or spec.categories
    try:
        if primitive == "combo_trend":
            return _trend(spec, periods)
        if primitive == "band_history":
            return _band(spec, periods)
        if primitive == "surprise":
            return _surprise(spec, periods)
        if primitive == "peer_bar":
            return _peer(spec)
        if primitive == "stacked_bar":
            return _stack(spec, periods)
        if primitive == "waterfall":
            return _waterfall(spec)
        if primitive == "heatmap":
            return _heatmap(spec)
        if primitive == "scatter_xy":
            return _scatter(spec)
    except Exception:
        return ""
    return ""


PERIODS_PER_YEAR = {"quarter": 4, "month": 12, "year": 1, "half": 2}


def periodicity(periods: Sequence[str]) -> int:
    if not periods:
        return 0
    sample = " ".join(str(p) for p in periods[:4]).upper()
    if any(f"{q}Q" in sample for q in "1234"):
        return 4
    months = ("JAN", "FEB", "MAR", "APR", "MAY", "JUN",
              "JUL", "AUG", "SEP", "OCT", "NOV", "DEC")
    if any(m in sample for m in months):
        return 12
    if sample.startswith("FY") or "FY" in sample:
        return 1
    return 0


def _cagr(first: float, last: float, n_steps: int, ppy: int) -> Number:
    if not ppy or n_steps <= 0 or first <= 0 or last <= 0:
        return None
    years = n_steps / ppy
    if years < 1.0:
        return None
    return ((last / first) ** (1.0 / years) - 1.0) * 100.0


def _reported(s) -> Sequence[Number]:
    ef = getattr(s, "estimate_from", None)
    if ef is None or ef <= 0 or ef >= len(s.values):
        return list(s.values)
    return list(s.values[:ef])


def _trend(spec, periods) -> str:
    left = [s for s in spec.series if s.axis == "left"] or spec.series
    s = left[0]
    actual = _reported(s)
    li, lv = _last(actual)
    fi, fv = _first(actual)
    if lv is None:
        return ""
    parts = [f"{s.name} {_v(lv, s.unit)} in {_label(periods, li)}"]
    ppy = periodicity(periods)

    right = [x for x in spec.series
             if x.axis == "right" and (x.derive or {}).get("op") == "yoy"]
    yoy_said = False
    if right:
        ri, rv = _last(right[0].values)
        if rv is not None and ri == li:
            parts.append(f"{fmt.fmt_value(rv, right[0].unit, signed=True)} YoY")
            yoy_said = True
    if not yoy_said and ppy and li is not None and li - ppy >= 0:
        base = actual[li - ppy]
        if _pp(s.unit):
            if base is not None:
                parts.append(f"{lv - base:+.1f}pp YoY")
        else:
            ch = _pct_change(lv, base)
            if ch is not None:
                parts.append(f"{ch:+.1f}% YoY")

    like_for_like = (fi is not None and li is not None and li != fi
                     and (not ppy or (li - fi) % ppy == 0))
    if like_for_like:
        span = li - fi
        unit_word = {4: "quarters", 12: "months", 1: "years", 2: "halves"}.get(ppy, "periods")
        if _pp(s.unit):
            parts.append(f"{lv - fv:+.1f}pp over {span} {unit_word}")
        else:
            ch = _pct_change(lv, fv)
            if ch is not None:
                parts.append(f"{ch:+.1f}% over {span} {unit_word}")
            cg = _cagr(fv, lv, span, ppy)
            if cg is not None:
                parts.append(f"{cg:+.1f}% CAGR")
    elif fi is not None and li is not None and li != fi and _pp(s.unit):
        unit_word = {4: "quarters", 12: "months", 1: "years",
                     2: "halves"}.get(ppy, "periods")
        parts.append(f"{lv - fv:+.1f}pp over {li - fi} {unit_word}")

    if len(actual) < len(s.values):
        ei, ev = _last(s.values)
        if ev is not None and ei is not None:
            parts.append(f"consensus {_v(ev, s.unit)} by {_label(periods, ei)}")

    parts.append(_coverage(actual))
    return _join(parts)


def _band(spec, periods) -> str:
    s = spec.series[0]
    li, lv = _last(s.values)
    if lv is None:
        return ""
    vals = [v for v in s.values if v is not None]
    med = fmt.median(vals)
    parts = [f"Current {_v(lv, s.unit)}"]
    if med:
        gap = _pct_change(lv, med)
        parts.append(f"median {_v(med, s.unit)}"
                     + (f" ({gap:+.0f}%)" if gap is not None else ""))
    pctl = _percentile_of(lv, s.values)
    if pctl is not None:
        parts.append(f"{_ordinal(round(pctl))} percentile of the period")
    lo, hi = min(vals), max(vals)
    parts.append(f"range {_v(lo, s.unit)}–{_v(hi, s.unit)}")
    return _join(parts)


def _surprise(spec, periods) -> str:
    from .primitives import _surprise_series
    s = _surprise_series(spec)
    vals = [v for v in s.values if v is not None]
    if not vals:
        return ""
    beats = sum(1 for v in vals if v >= 0)
    avg = sum(vals) / len(vals)
    li, lv = _last(s.values)
    parts = [f"Beat in {beats} of {len(vals)} quarter(s)",
             f"average {avg:+.1f}%"]
    if lv is not None:
        parts.append(f"latest {lv:+.1f}% in {_label(periods, li)}")
    return _join(parts)


def _peer(spec) -> str:
    s = spec.series[0]
    cats = spec.categories or spec.periods
    target = str(spec.opt("highlight", spec.ticker) or "").upper()
    pairs = [(c, v) for c, v in zip(cats, s.values) if v is not None]
    if not pairs:
        return ""
    mine = next((v for c, v in pairs if c.upper() == target), None)
    peers = [v for c, v in pairs if c.upper() != target]
    parts = []
    if mine is not None:
        parts.append(f"{target} {_v(mine, s.unit)}")
        if peers:
            med = fmt.median(peers)
            if med is not None:
                if _pp(s.unit):
                    parts.append(f"peer median {_v(med, s.unit)} "
                                 f"({mine - med:+.1f}pp)")
                else:
                    gap = _pct_change(mine, med)
                    parts.append(f"peer median {_v(med, s.unit)}"
                                 + (f" ({gap:+.0f}%)" if gap is not None else ""))
        rank = sorted((v for _, v in pairs), reverse=True).index(mine) + 1
        parts.append(f"ranks {rank} of {len(pairs)}")
    else:
        med = fmt.median([v for _, v in pairs])
        parts.append(f"median {_v(med, s.unit)} across {len(pairs)}")
    return _join(parts)


def _stack(spec, periods) -> str:
    idx = None
    for i in range(len(periods) - 1, -1, -1):
        if any(s.values[i] is not None for s in spec.series if i < len(s.values)):
            idx = i
            break
    if idx is None:
        return ""
    contrib = [(s.name, s.values[idx]) for s in spec.series
               if idx < len(s.values) and s.values[idx] is not None]
    if not contrib:
        return ""
    total = sum(v for _, v in contrib)
    top, tv = max(contrib, key=lambda kv: kv[1])
    parts = [f"{_label(periods, idx)} total "
             f"{fmt.fmt_value(total, spec.series[0].unit, compact=True)}"]
    if total:
        parts.append(f"largest {top} at {tv / total * 100:.0f}%")
    return _join(parts)


def _waterfall(spec) -> str:
    s = spec.series[0]
    cats = spec.categories or spec.periods
    pairs = [(c, v) for c, v in zip(cats, s.values) if v is not None]
    if not pairs:
        return ""
    uses = [(c, v) for c, v in pairs if v < 0]
    parts = [f"{pairs[0][0]} {fmt.fmt_value(pairs[0][1], s.unit, compact=True)}"]
    if uses:
        c, v = min(uses, key=lambda kv: kv[1])
        parts.append(f"largest use {c} {fmt.fmt_value(abs(v), s.unit, compact=True)}")
    parts.append(f"{pairs[-1][0]} "
                 f"{fmt.fmt_value(pairs[-1][1], s.unit, compact=True, signed=True)}")
    return _join(parts)


def _heatmap(spec) -> str:
    cells = [v for row in spec.matrix for v in row if v is not None]
    if not cells:
        return ""
    pos = sum(1 for v in cells if v >= 0)
    unit = spec.units.get("left") or "pct"
    return _join([f"{pos} of {len(cells)} cells positive",
                  f"range {_v(min(cells), unit)}–{_v(max(cells), unit)}"])


def _scatter(spec) -> str:
    xs = next((s for s in spec.series if s.meta.get("role") == "x"), spec.series[0])
    ys = next((s for s in spec.series if s.meta.get("role") == "y"),
              spec.series[-1])
    cats = spec.categories or spec.periods
    target = str(spec.opt("highlight", spec.ticker) or "").upper()
    i = next((k for k, c in enumerate(cats) if str(c).upper() == target), None)
    if i is None or i >= len(xs.values) or i >= len(ys.values):
        return ""
    xv, yv = xs.values[i], ys.values[i]
    if xv is None or yv is None:
        return ""
    mx = fmt.median([v for v in xs.values if v is not None])
    my = fmt.median([v for v in ys.values if v is not None])
    side_x = "above" if mx is not None and xv >= mx else "below"
    side_y = "above" if my is not None and yv >= my else "below"
    return _join([f"{target} {_v(yv, ys.unit)} on "
                  f"{_v(xv, xs.unit)}",
                  f"{side_y} median on {ys.name.lower()}",
                  f"{side_x} median on {xs.name.lower()}"])
