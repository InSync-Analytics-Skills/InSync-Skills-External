"""The eight drawing primitives. Every supported chart type maps onto one of these.

Keeping the primitive count small is what makes the library visually consistent:
a bar is drawn by exactly one function, everywhere, forever.
"""
from __future__ import annotations

from typing import List, Optional, Tuple

import matplotlib.patheffects as pe
import matplotlib.pyplot as plt
from matplotlib.colors import LinearSegmentedColormap, TwoSlopeNorm
from matplotlib.figure import Figure

from . import chrome, fmt, theme
from .spec import ChartSpec, Series

Handles = Tuple[List, List[str]]


def _color_for(s: Series, i: int, spec: Optional[ChartSpec] = None) -> str:
    if s.color:
        return s.color
    reserve = bool(spec) and any(x.focus for x in spec.series) and not s.focus
    return theme.series_color(i, focus=s.focus, reserve_brand=reserve)


def _x_labels(spec: ChartSpec) -> List[str]:
    return spec.periods or spec.categories or [str(i + 1) for i in range(
        len(spec.series[0].values) if spec.series else 0)]


def draw_bar(target: plt.Axes, x, height, *, bottom=0.0, width=0.6, color: str,
             hatch: Optional[str] = None, alpha: float = 1.0, zorder: int = 3,
             horizontal: bool = False):
    kw = dict(color=color, alpha=alpha, zorder=zorder, hatch=hatch,
              edgecolor=theme.BG if hatch else "none",
              linewidth=0.0 if hatch else 0.0)
    if horizontal:
        return target.barh(x, height, left=bottom, height=width, **kw)
    return target.bar(x, height, bottom=bottom, width=width, **kw)


def _endpoint_label(ax: plt.Axes, s: Series, spec: ChartSpec, color: str) -> None:
    idx = next((i for i in range(len(s.values) - 1, -1, -1)
                if s.values[i] is not None), None)
    if idx is None:
        return
    v = s.values[idx]
    text = fmt.fmt_value(v, s.unit, compact=bool(spec.opt("compact_labels", True)))
    periods = spec.periods or []
    if idx < len(s.values) - 1 and idx < len(periods):
        text = f"{text}\n({periods[idx]})"
    ax.annotate(text, (idx, v), textcoords="offset points", xytext=(9, 0),
                ha="left", va="center", fontsize=theme.FS_DATALABEL,
                color=color, fontweight="bold", fontfamily=theme.SANS,
                linespacing=1.25, clip_on=False, zorder=7,
                path_effects=[pe.withStroke(linewidth=1.8, foreground=theme.BG)])


def _first_point_marker(ax: plt.Axes, s: Series, periods: List[str]) -> None:
    if s.derive:
        return
    idx = next((i for i, v in enumerate(s.values) if v is not None), None)
    if not idx:
        return
    label = periods[idx] if idx < len(periods) else ""
    ax.annotate(f"data from {label}" if label else "series begins here",
                (idx, s.values[idx]), textcoords="offset points",
                xytext=(7, 9), ha="left", va="bottom",
                fontsize=theme.FS_SOURCE, color=theme.GREY_LT,
                fontfamily=theme.SANS, style="italic", clip_on=False, zorder=6,
                path_effects=[pe.withStroke(linewidth=1.6, foreground=theme.BG)])


def _is_primary(spec: ChartSpec, s: Series) -> bool:
    bars = [x for x in spec.series if x.kind == "bar" and x.axis == "left"]
    if bars:
        return s is bars[0] and len({b.stack_group for b in bars}) >= len(bars)
    left = [x for x in spec.series if x.axis == "left"]
    return bool(left) and s is left[0]


def _crowded(spec: ChartSpec, s: Series) -> bool:
    others = [o for o in spec.series
              if o is not s and o.axis == s.axis and o.kind != "bar"]
    if not others:
        return False
    vals = [v for ser in spec.series if ser.axis == s.axis
            for v in ser.values if v is not None]
    if len(vals) < 2:
        return False
    span = (max(vals) - min(vals)) or 1.0
    for o in others:
        for a, b in zip(s.values, o.values):
            if a is None or b is None:
                continue
            if abs(a - b) / span < 0.14:
                return True
    return False


def _should_label(spec: ChartSpec, s: Series, n_points: int,
                  width_in: float = 6.5) -> bool:
    if s.show_labels is not None:
        return bool(s.show_labels)
    default = spec.opt("data_labels", "auto")
    if default is True:
        return True
    if default is False:
        return False
    if not _is_primary(spec, s):
        return False
    if s.kind == "bar" and any(o.axis == "right" and o.kind != "bar"
                               for o in spec.series):
        return False
    if s.kind != "bar" and _crowded(spec, s):
        return False
    return n_points <= 10 and width_in >= 4.4 and (width_in / max(n_points, 1)) >= 0.55


def _fmt_pt(s: Series, v, spec: ChartSpec, idx: int) -> str:
    if s.labels and idx < len(s.labels) and s.labels[idx] is not None:
        return str(s.labels[idx])
    return fmt.fmt_value(v, s.unit, dp=spec.opt("label_dp"),
                         compact=bool(spec.opt("compact_labels", True)))


def combo_trend(fig: Figure, ax: plt.Axes, spec: ChartSpec) -> Handles:
    labels = _x_labels(spec)
    n = len(labels)
    wi = float(getattr(ax, "_insync", {}).get("w", 6.5))
    bars = [s for s in spec.series if s.kind == "bar"]
    others = [s for s in spec.series if s.kind != "bar"]
    handles, names = [], []

    est_from = next((s.estimate_from for s in spec.series if s.estimate_from is not None), None)
    if est_from is not None:
        chrome.estimate_shading(ax, int(est_from), n, str(spec.opt("estimate_label", "Estimates")))

    ax2 = chrome.secondary_axis(ax) if any(s.axis == "right" for s in spec.series) else None
    chrome.register_secondary(ax, ax2)

    groups = {}
    for s in bars:
        groups.setdefault(s.stack_group or f"__{id(s)}", []).append(s)
    gkeys = list(groups.keys())
    ng = len(gkeys)
    width = min(0.72, 0.82 / max(ng, 1)) if ng else 0.6
    for gi, gk in enumerate(gkeys):
        offset = (gi - (ng - 1) / 2.0) * width if ng > 1 else 0.0
        pos_base = [0.0] * n
        neg_base = [0.0] * n
        for s in groups[gk]:
            si = spec.series.index(s)
            col = _color_for(s, si, spec)
            target = ax2 if (s.axis == "right" and ax2 is not None) else ax
            xs, hs, bs, cols, hatches = [], [], [], [], []
            for i, v in enumerate(s.values):
                if v is None:
                    chrome.na_marker(target, i + offset)
                    continue
                base = pos_base[i] if v >= 0 else neg_base[i]
                xs.append(i + offset); hs.append(v); bs.append(base)
                is_est = s.estimate_from is not None and i >= s.estimate_from
                cols.append(col)
                hatches.append(theme.ESTIMATE_HATCH if is_est else theme.series_hatch(si))
                if v >= 0:
                    pos_base[i] += v
                else:
                    neg_base[i] += v
            for x, hgt, b, c, hh in zip(xs, hs, bs, cols, hatches):
                draw_bar(target, x, hgt, bottom=b, width=width * 0.86, color=c,
                         hatch=hh, alpha=s.alpha if s.alpha is not None else 1.0)
            if _should_label(spec, s, n, wi) and len(groups[gk]) == 1:
                for i, v in enumerate(s.values):
                    if v is None:
                        continue
                    chrome.data_label(target, i + offset, v, _fmt_pt(s, v, spec, i),
                                      above=v >= 0, color=theme.INK)
            handles.append(chrome.legend_patch(col, theme.series_hatch(si)))
            names.append(s.name)

    for s in others:
        si = spec.series.index(s)
        col = _color_for(s, si, spec)
        target = ax2 if (s.axis == "right" and ax2 is not None) else ax
        ls = "-" if s.kind == "area" else theme.series_style(si)
        mk = theme.series_marker(si) if (n <= 20 and s.kind != "area") else None
        xs = list(range(n))
        ys = list(s.values)
        if s.kind == "marker":
            pts = [(x, y) for x, y in zip(xs, ys) if y is not None]
            if pts:
                target.scatter([p[0] for p in pts], [p[1] for p in pts], s=26,
                               color=col, zorder=5, edgecolors=theme.BG, linewidths=0.7)
            handles.append(chrome.legend_line(col, "none", mk or "o"))
        else:
            target.plot(xs, ys, color=col, linestyle=ls, marker=mk,
                        linewidth=s.linewidth or 1.7, zorder=4,
                        markeredgecolor=theme.BG, markeredgewidth=0.7,
                        alpha=s.alpha if s.alpha is not None else 1.0)
            if s.kind == "area":
                clean_pts = [(x, y) for x, y in zip(xs, ys) if y is not None]
                if clean_pts:
                    target.fill_between([p[0] for p in clean_pts], 0,
                                        [p[1] for p in clean_pts],
                                        color=col, alpha=0.13, zorder=2, linewidth=0)
            handles.append(chrome.legend_line(col, ls, mk))
        names.append(s.name)
        if _should_label(spec, s, n, wi):
            for i, v in enumerate(ys):
                if v is not None:
                    chrome.data_label(target, i, v, _fmt_pt(s, v, spec, i), color=theme.GREY)
        elif spec.opt("endpoint_labels", True) and s.kind in ("line", "step", "area"):
            _endpoint_label(target, s, spec, col)
        if (spec.opt("mark_late_start", True) and s.kind != "bar"
                and _is_primary(spec, s)):
            _first_point_marker(target, s, labels)

    chrome.grid(ax)
    chrome.category_ticks(ax, labels)
    if ax2 is not None:
        ax2.set_xlim(ax.get_xlim())
        ax2.yaxis.set_major_formatter(fmt.axis_formatter(spec.units.get("right")))
        chrome.headroom(ax2, top=0.08, bottom=float(spec.opt("right_axis_lift", 0.45)))
    ax.yaxis.set_major_formatter(fmt.axis_formatter(spec.units.get("left")))
    chrome.headroom(ax, top=0.30 if ax2 is not None else 0.14)
    chrome.zero_line(ax)
    chrome.axis_titles(ax, spec, ax2)
    return handles, names


def stacked_bar(fig: Figure, ax: plt.Axes, spec: ChartSpec) -> Handles:
    labels = _x_labels(spec)
    n = len(labels)
    series = list(spec.series)
    normalise = bool(spec.opt("normalise", spec.chart_type.endswith("mix")))

    vals = [[0.0 if v is None else v for v in s.values] for s in series]
    if normalise:
        totals = [sum(col) for col in zip(*vals)] if vals else []
        vals = [[(v / t * 100.0 if t else 0.0) for v, t in zip(row, totals)] for row in vals]

    handles, names = [], []
    pos_base = [0.0] * n
    neg_base = [0.0] * n
    ns = len(series)
    hatch_from = int(spec.opt("hatch_from", len(theme.STACK_RAMP)))
    min_label_frac = 0.055
    span = max((abs(sum(x for x in col if x > 0)) + abs(sum(x for x in col if x < 0))
                for col in zip(*vals)), default=1.0) or 1.0
    for si, (s, row) in enumerate(zip(series, vals)):
        col = s.color or (theme.ORANGE if s.focus else theme.stack_color(si, ns))
        hatch = theme.series_hatch(si) if si >= hatch_from else None
        bottoms = [pos_base[i] if v >= 0 else neg_base[i] + v
                   for i, v in enumerate(row)]
        ax.bar(range(n), [abs(v) for v in row], bottom=bottoms, width=0.66,
               color=col, edgecolor=theme.BG, linewidth=0.6, hatch=hatch, zorder=3)
        if spec.opt("segment_labels", n <= 8 and ns > 1):
            lum = _luminance(col)
            for i, (v, b) in enumerate(zip(row, bottoms)):
                if abs(v) / span < min_label_frac:
                    continue
                ax.text(i, b + abs(v) / 2.0,
                        fmt.fmt_value(v, "pct" if normalise else s.unit,
                                      compact=not normalise),
                        ha="center", va="center", fontsize=theme.FS_DATALABEL,
                        color=theme.BG if lum < 0.55 else theme.INK,
                        fontfamily=theme.SANS, zorder=5)
        for i, v in enumerate(row):
            if v >= 0:
                pos_base[i] += v
            else:
                neg_base[i] += v
        handles.append(chrome.legend_patch(col, hatch))
        names.append(s.name)

    base = [p + q for p, q in zip(pos_base, neg_base)]
    if spec.opt("total_labels", not normalise and n <= 12):
        for i, t in enumerate(base):
            chrome.data_label(ax, i, max(pos_base[i], t),
                              fmt.fmt_value(t, series[0].unit, compact=True))

    chrome.grid(ax)
    chrome.category_ticks(ax, labels)
    unit = "pct" if normalise else spec.units.get("left")
    ax.yaxis.set_major_formatter(fmt.axis_formatter(unit))
    if normalise and min(neg_base) == 0.0:
        ax.set_ylim(0, 100)
        spec.axis_labels.setdefault("left", "% of total")
    else:
        chrome.headroom(ax)
    chrome.zero_line(ax)
    chrome.axis_titles(ax, spec)
    return handles, names


def _luminance(hex_color: str) -> float:
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def band_history(fig: Figure, ax: plt.Axes, spec: ChartSpec) -> Handles:
    labels = _x_labels(spec)
    n = len(labels)
    handles, names = [], []

    for si, s in enumerate(spec.series):
        col = _color_for(s, si if not s.focus else 0, spec)
        xs = list(range(n))
        ax.plot(xs, s.values, color=col, linewidth=1.9,
                linestyle=theme.series_style(si), zorder=5)
        handles.append(chrome.legend_line(col, theme.series_style(si)))
        names.append(s.name)

        if si == 0 and spec.opt("stat_bands", True):
            robust = bool(spec.opt("robust_bands", True))
            lo_p, hi_p = (spec.opt("band_percentiles", (10, 90)) if robust
                          else (None, None))
            centre = fmt.median(s.values) if robust else fmt.mean(s.values)
            c_name = "Period median" if robust else "Period mean"
            if centre is not None:
                ax.axhline(centre, color=theme.GREY, linewidth=1.0,
                           linestyle="--", zorder=3)
                ax.annotate(f"{'Median' if robust else 'Mean'} "
                            f"{fmt.fmt_value(centre, s.unit)}",
                            xy=(0.0, centre), xycoords=("axes fraction", "data"),
                            xytext=(4, 3), textcoords="offset points",
                            ha="left", va="bottom", fontsize=theme.FS_SOURCE,
                            color=theme.GREY, fontfamily=theme.SANS)
                handles.append(chrome.legend_line(theme.GREY, "--"))
                names.append(c_name)

            if robust:
                lo = fmt.percentile(s.values, float(lo_p))
                hi = fmt.percentile(s.values, float(hi_p))
                band_name = f"P{int(lo_p)}–P{int(hi_p)}"
                tags = ((hi, f"P{int(hi_p)}", "bottom", 2),
                        (lo, f"P{int(lo_p)}", "top", -3))
            else:
                sd = fmt.stdev(s.values)
                lo = None if (centre is None or not sd) else centre - sd
                hi = None if (centre is None or not sd) else centre + sd
                band_name = "±1 s.d."
                tags = ((hi, "+1 s.d.", "bottom", 2), (lo, "−1 s.d.", "top", -3))

            if lo is not None and hi is not None and hi > lo:
                ax.axhspan(lo, hi, color=theme.SERIES[2], alpha=0.11,
                           zorder=1, linewidth=0)
                for y, tag, va, dy in tags:
                    ax.axhline(y, color=theme.HAIRLINE, linewidth=0.8,
                               linestyle=":", zorder=2)
                    ax.annotate(f"{tag} {fmt.fmt_value(y, s.unit)}",
                                xy=(0.0, y), xycoords=("axes fraction", "data"),
                                xytext=(4, dy), textcoords="offset points",
                                ha="left", va=va, fontsize=theme.FS_SOURCE,
                                color=theme.GREY_LT, fontfamily=theme.SANS)
                handles.append(chrome.legend_patch(theme.SERIES[2], alpha=0.2))
                names.append(band_name)

        if si == 0 and spec.opt("mark_outliers", True):
            from . import dataquality as _dq
            for oi in _dq.outliers(s.values)[:3]:
                ov = s.values[oi]
                lbl = labels[oi] if oi < len(labels) else ""
                ax.scatter([oi], [ov], s=30, facecolors="none",
                           edgecolors=theme.NEG, linewidths=1.1, zorder=7)
                ax.annotate(f"{lbl} {fmt.fmt_value(ov, s.unit)}", (oi, ov),
                            textcoords="offset points", xytext=(0, 9),
                            ha="center", va="bottom", fontsize=theme.FS_SOURCE,
                            color=theme.NEG, fontfamily=theme.SANS,
                            path_effects=[pe.withStroke(linewidth=1.8,
                                                        foreground=theme.BG)])

        if si == 0 and spec.opt("mark_current", True):
            last = next(((i, v) for i, v in reversed(list(enumerate(s.values)))
                         if v is not None), None)
            if last:
                i, v = last
                ax.scatter([i], [v], s=42, color=theme.ORANGE, zorder=7,
                           edgecolors=theme.BG, linewidths=1.0)
                mu = fmt.mean(s.values)
                below = mu is not None and v >= mu
                ax.annotate(f"Current {fmt.fmt_value(v, s.unit)}", (i, v),
                            textcoords="offset points",
                            xytext=(-7, -10 if below else 9),
                            ha="right", va="top" if below else "bottom",
                            fontsize=theme.FS_DATALABEL,
                            color=theme.ORANGE_DK, fontweight="bold",
                            fontfamily=theme.SANS)

    chrome.grid(ax)
    chrome.category_ticks(ax, labels, max_ticks=12)
    ax.yaxis.set_major_formatter(fmt.axis_formatter(spec.units.get("left"), compact=False))
    chrome.headroom(ax, top=0.16, bottom=0.10)
    chrome.axis_titles(ax, spec)
    return handles, names


def _surprise_series(spec: ChartSpec) -> Series:
    for s in spec.series:
        if (s.derive or {}).get("op") == "surprise_pct":
            return s
    pct = [s for s in spec.series if (s.unit or "") in ("pct", "pct_pt", "bps")]
    if pct:
        return next((s for s in pct if s.focus), pct[0])
    return spec.series[0]


def surprise(fig: Figure, ax: plt.Axes, spec: ChartSpec) -> Handles:
    labels = _x_labels(spec)
    n = len(labels)
    main = _surprise_series(spec)
    abs_beat, abs_unit = [], None
    ab = spec.opt("abs_beat_from") or {}
    by_name = {x.name: x for x in spec.series}
    a_s, e_s = by_name.get(ab.get("actual")), by_name.get(ab.get("estimate"))
    if a_s and e_s:
        abs_unit = a_s.unit
        abs_beat = [None if (a is None or e is None) else a - e
                    for a, e in zip(a_s.values, e_s.values)]
    pos_c = spec.opt("positive_color", theme.POS)
    neg_c = spec.opt("negative_color", theme.NEG)

    for i, v in enumerate(main.values):
        if v is None:
            chrome.na_marker(ax, i)
            continue
        draw_bar(ax, i, v, width=0.68, color=pos_c if v >= 0 else neg_c,
                 hatch=None if v >= 0 else "///")
        txt = fmt.fmt_value(v, main.unit or "pct", signed=True)
        if abs_beat and i < len(abs_beat) and abs_beat[i] is not None:
            txt += f"\n{fmt.fmt_value(abs_beat[i], abs_unit, signed=True)}"
        chrome.data_label(ax, i, v, txt, above=v >= 0,
                          color=pos_c if v >= 0 else neg_c)

    present = {v >= 0 for v in main.values if v is not None}
    handles, names = [], []
    if True in present:
        handles.append(chrome.legend_patch(pos_c))
        names.append(spec.opt("positive_label", "Beat"))
    if False in present:
        handles.append(chrome.legend_patch(neg_c, "///"))
        names.append(spec.opt("negative_label", "Miss"))
    inputs = {ab.get("actual"), ab.get("estimate")} - {None}
    for s in spec.series:
        if s is main or s.name in inputs:
            continue
        ax.plot(range(n), s.values, color=theme.INK, linestyle="--",
                linewidth=1.3, zorder=5)
        handles.append(chrome.legend_line(theme.INK, "--"))
        names.append(s.name)

    hit = [v for v in main.values if v is not None]
    if hit and spec.opt("mean_surprise_line", True) and len(hit) >= 3:
        avg = sum(hit) / len(hit)
        ax.axhline(avg, color=theme.INK, linestyle="--", linewidth=1.0, zorder=5)
        ax.annotate(f"Average {avg:+.1f}%",
                    xy=(0.0, avg), xycoords=("axes fraction", "data"),
                    xytext=(4, 3 if avg >= 0 else -3), textcoords="offset points",
                    ha="left", va="bottom" if avg >= 0 else "top",
                    fontsize=theme.FS_SOURCE, color=theme.INK,
                    fontfamily=theme.SANS,
                    path_effects=[pe.withStroke(linewidth=1.8, foreground=theme.BG)])
        handles.append(chrome.legend_line(theme.INK, "--"))
        names.append("Average")

    if hit and spec.opt("hit_rate", True):
        beats = sum(1 for v in hit if v >= 0)
        rate = beats / len(hit) * 100.0
        biggest = max(hit, key=abs)
        strip = (f"Beat {beats} of {len(hit)}  ({rate:.0f}%)     "
                 f"Average {sum(hit) / len(hit):+.1f}%     "
                 f"Largest {biggest:+.1f}%")
        ax.annotate(strip, xy=(1.0, 1.0), xycoords="axes fraction",
                    xytext=(-2, -4), textcoords="offset points",
                    ha="right", va="top", fontsize=theme.FS_SOURCE,
                    color=theme.INK, fontfamily=theme.SANS, fontweight="bold",
                    path_effects=[pe.withStroke(linewidth=2.0, foreground=theme.BG)])

    chrome.grid(ax)
    chrome.category_ticks(ax, labels)
    ax.yaxis.set_major_formatter(fmt.axis_formatter(main.unit or "pct", compact=False))
    chrome.headroom(ax, top=0.28, bottom=0.20)
    chrome.zero_line(ax)
    chrome.axis_titles(ax, spec)
    return handles, names


def peer_bar(fig: Figure, ax: plt.Axes, spec: ChartSpec) -> Handles:
    cats = spec.categories or spec.periods
    s = spec.series[0]
    highlight = str(spec.opt("highlight", spec.ticker) or "").upper()
    order = spec.opt("sort", "desc")

    rows = [(c, v) for c, v in zip(cats, s.values)]
    if order in ("desc", "asc"):
        rows.sort(key=lambda r: (r[1] is None, -(r[1] or 0) if order == "desc" else (r[1] or 0)))
    rows = rows[::-1]

    base_c = s.color or theme.SERIES[0]
    ys = list(range(len(rows)))
    for y, (c, v) in zip(ys, rows):
        is_target = c.upper() == highlight
        col = theme.ORANGE if is_target else base_c
        if v is None:
            chrome.na_marker(ax, 0, y, ha="left", dx=4)
            continue
        draw_bar(ax, y, v, width=0.62, color=col, horizontal=True,
                 alpha=1.0 if is_target else 0.88)
        pad = 4 if v >= 0 else -4
        ax.annotate(fmt.fmt_value(v, s.unit, compact=bool(spec.opt("compact_labels", True))),
                    (v, y), textcoords="offset points", xytext=(pad, 0),
                    ha="left" if v >= 0 else "right", va="center",
                    fontsize=theme.FS_DATALABEL,
                    color=theme.ORANGE_DK if is_target else theme.INK,
                    fontweight="bold" if is_target else "normal",
                    fontfamily=theme.SANS, clip_on=False)

    ax.set_yticks(ys)
    ax.set_yticklabels([c for c, _ in rows], fontsize=theme.FS_TICK)
    for lbl, (c, _) in zip(ax.get_yticklabels(), rows):
        if c.upper() == highlight:
            lbl.set_color(theme.ORANGE_DK)
            lbl.set_fontweight("bold")
        else:
            lbl.set_color(theme.GREY)

    handles, names = [], []
    stat = spec.opt("reference_line", "median")
    peer_vals = [v for c, v in rows if v is not None and c.upper() != highlight]
    all_vals = [v for _, v in rows if v is not None]
    vals = peer_vals if peer_vals else all_vals
    tag = "Peer" if peer_vals else "Group"
    if stat and vals:
        ref = fmt.median(vals) if stat == "median" else fmt.mean(vals)
        ax.axvline(ref, color=theme.INK, linestyle="--", linewidth=1.0, zorder=5)
        ax.annotate(f"{tag} {stat} {fmt.fmt_value(ref, s.unit)}",
                    xy=(ref, 1.0), xycoords=("data", "axes fraction"),
                    xytext=(4, -3), textcoords="offset points", ha="left", va="top",
                    fontsize=theme.FS_SOURCE, color=theme.INK, fontfamily=theme.SANS,
                    path_effects=[pe.withStroke(linewidth=1.8, foreground=theme.BG)])
        handles.append(chrome.legend_line(theme.INK, "--"))
        names.append(f"{tag} {stat}")

    if highlight and any(c.upper() == highlight for c, _ in rows):
        handles = [chrome.legend_patch(theme.ORANGE), chrome.legend_patch(base_c)] + handles
        names = [highlight, "Peers"] + names

    ax.grid(False)
    ax.grid(True, axis="x", color=theme.GRID, linewidth=0.6, zorder=0)
    ax.set_axisbelow(True)
    for side in ("top", "right", "bottom"):
        ax.spines[side].set_visible(False)
    ax.spines["left"].set_color(theme.AXIS)
    ax.tick_params(axis="both", length=0)
    ax.xaxis.set_major_formatter(fmt.axis_formatter(s.unit))
    ax.set_ylim(-0.7, len(rows) - 0.3)
    lo, hi = ax.get_xlim()
    ax.set_xlim(lo, hi + (hi - lo) * 0.10)
    left = fmt.axis_label(s.unit, spec.axis_labels.get("x"))
    if left:
        ax.set_xlabel(left, color=theme.GREY, fontsize=theme.FS_AXIS_LABEL, labelpad=6)
    return handles, names


def waterfall(fig: Figure, ax: plt.Axes, spec: ChartSpec) -> Handles:
    cats = spec.categories or spec.periods
    s = spec.series[0]
    totals = set(int(i) for i in (spec.opt("total_indices") or []))
    if spec.opt("last_is_total", True) and cats:
        totals.add(len(cats) - 1)
    if spec.opt("first_is_total", True) and cats:
        totals.add(0)

    running = 0.0
    bar_lo: List[float] = []
    bar_hi: List[float] = []
    kinds: List[str] = []
    for i, v in enumerate(s.values):
        v = 0.0 if v is None else v
        if i in totals:
            bar_lo.append(0.0); bar_hi.append(v); kinds.append("total")
            running = v
        else:
            lo, hi = (running, running + v) if v >= 0 else (running + v, running)
            bar_lo.append(lo); bar_hi.append(hi)
            kinds.append("up" if v >= 0 else "down")
            running += v

    col_map = {"total": spec.opt("total_color", theme.TOTAL),
               "up": spec.opt("positive_color", theme.POS),
               "down": spec.opt("negative_color", theme.NEG)}
    hatch_map = {"total": None, "up": None, "down": "///"}

    for i, (lo, hi, k) in enumerate(zip(bar_lo, bar_hi, kinds)):
        draw_bar(ax, i, hi - lo, bottom=lo, width=0.62, color=col_map[k],
                 hatch=hatch_map[k])
        v = s.values[i]
        if v is None:
            chrome.na_marker(ax, i, lo)
            continue
        top = hi if (hi - lo) >= 0 else lo
        chrome.data_label(ax, i, top,
                          fmt.fmt_value(v, s.unit, compact=bool(spec.opt("compact_labels", True)),
                                        signed=(k != "total")),
                          above=True,
                          color=col_map[k] if k != "total" else theme.INK)
        if i < len(cats) - 1:
            ax.plot([i + 0.31, i + 1 - 0.31], [running_edge(bar_lo, bar_hi, kinds, i)] * 2,
                    color=theme.GREY_LT, linewidth=0.7, linestyle=":", zorder=2)

    chrome.grid(ax)
    chrome.category_ticks(ax, cats)
    ax.yaxis.set_major_formatter(fmt.axis_formatter(s.unit))
    chrome.headroom(ax, top=0.18, bottom=0.10)
    chrome.zero_line(ax)
    chrome.axis_titles(ax, spec)

    present = set(kinds)
    entries = [("total", None, spec.opt("total_label", "Total")),
               ("up", None, spec.opt("positive_label", "Source / increase")),
               ("down", "///", spec.opt("negative_label", "Use / decrease"))]
    handles = [chrome.legend_patch(col_map[k], h) for k, h, _ in entries if k in present]
    names = [lbl for k, _, lbl in entries if k in present]
    return handles, names


def running_edge(lo: List[float], hi: List[float], kinds: List[str], i: int) -> float:
    if kinds[i] == "total":
        return hi[i]
    return hi[i] if kinds[i] == "up" else lo[i]


def scatter_xy(fig: Figure, ax: plt.Axes, spec: ChartSpec) -> Handles:
    xs_series = next((s for s in spec.series if s.meta.get("role") == "x"), spec.series[0])
    ys_series = next((s for s in spec.series if s.meta.get("role") == "y"),
                     spec.series[1] if len(spec.series) > 1 else spec.series[0])
    cats = spec.categories or spec.periods
    highlight = str(spec.opt("highlight", spec.ticker) or "").upper()

    for i, (x, y) in enumerate(zip(xs_series.values, ys_series.values)):
        if x is None or y is None:
            continue
        name = cats[i] if i < len(cats) else ""
        is_t = name.upper() == highlight
        ax.scatter([x], [y], s=64 if is_t else 40,
                   color=theme.ORANGE if is_t else theme.SERIES[0],
                   edgecolors=theme.BG, linewidths=0.8, zorder=5 if is_t else 4)
        ax.annotate(name, (x, y), textcoords="offset points", xytext=(6, 4),
                    fontsize=theme.FS_DATALABEL,
                    color=theme.ORANGE_DK if is_t else theme.GREY,
                    fontweight="bold" if is_t else "normal", fontfamily=theme.SANS)

    if spec.opt("quadrants", True):
        mx = fmt.median([v for v in xs_series.values if v is not None])
        my = fmt.median([v for v in ys_series.values if v is not None])
        if mx is not None:
            ax.axvline(mx, color=theme.HAIRLINE, linestyle="--", linewidth=0.9, zorder=1)
        if my is not None:
            ax.axhline(my, color=theme.HAIRLINE, linestyle="--", linewidth=0.9, zorder=1)

    ax.grid(False)
    ax.grid(True, axis="both", color=theme.GRID, linewidth=0.6, zorder=0)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    ax.spines["left"].set_visible(True)
    ax.spines["left"].set_color(theme.AXIS)
    ax.margins(x=0.10, y=0.12)
    x_unit = spec.units.get("x") or xs_series.unit
    y_unit = ys_series.unit or spec.units.get("left")
    ax.xaxis.set_major_formatter(fmt.axis_formatter(x_unit, compact=False))
    ax.yaxis.set_major_formatter(fmt.axis_formatter(y_unit, compact=False))
    ax.set_xlabel(spec.axis_labels.get("x") or xs_series.name,
                  color=theme.GREY, fontsize=theme.FS_AXIS_LABEL, labelpad=6)
    ax.set_ylabel(spec.axis_labels.get("left") or ys_series.name,
                  color=theme.GREY, fontsize=theme.FS_AXIS_LABEL, labelpad=6)
    return [], []


def heatmap(fig: Figure, ax: plt.Axes, spec: ChartSpec) -> Handles:
    matrix = spec.matrix or []
    rows, cols = spec.row_labels, spec.col_labels
    scheme = spec.opt("color_scheme", "diverging")
    palette = theme.DIVERGING if scheme == "diverging" else theme.SEQUENTIAL
    cmap = LinearSegmentedColormap.from_list("insync_" + scheme, palette, N=256)

    flat = [v for r in matrix for v in r if v is not None]
    if not flat:
        raise ValueError("heatmap matrix contains no numeric values")
    vmin, vmax = min(flat), max(flat)
    if scheme == "diverging":
        m = max(abs(vmin), abs(vmax)) or 1.0
        norm = TwoSlopeNorm(vmin=-m, vcenter=0.0, vmax=m)
    else:
        norm = plt.Normalize(vmin=vmin, vmax=vmax)

    unit = spec.units.get("left") or "pct"
    nr, nc = len(matrix), len(matrix[0]) if matrix else 0
    for r in range(nr):
        for c in range(nc):
            v = matrix[r][c]
            face = theme.BG if v is None else cmap(norm(v))
            ax.add_patch(plt.Rectangle((c, nr - 1 - r), 1, 1, facecolor=face,
                                       edgecolor=theme.BG, linewidth=1.2, zorder=2))
            txt = fmt.NA_TEXT if v is None else fmt.fmt_value(
                v, unit, dp=spec.opt("label_dp"), signed=(scheme == "diverging"))
            lum = 1.0 if v is None else _luminance_rgba(cmap(norm(v)))
            ax.text(c + 0.5, nr - 1 - r + 0.5, txt, ha="center", va="center",
                    fontsize=theme.FS_DATALABEL,
                    color=theme.GREY_LT if v is None else (theme.BG if lum < 0.5 else theme.INK),
                    fontfamily=theme.SANS, zorder=3)

    ax.set_xlim(0, nc); ax.set_ylim(0, nr)
    ax.set_xticks([c + 0.5 for c in range(nc)])
    ax.set_xticklabels(cols or [str(c + 1) for c in range(nc)],
                       fontsize=theme.FS_TICK, color=theme.GREY)
    ax.set_yticks([nr - 1 - r + 0.5 for r in range(nr)])
    ax.set_yticklabels(rows or [str(r + 1) for r in range(nr)],
                       fontsize=theme.FS_TICK, color=theme.GREY)
    for lbl in ax.get_yticklabels():
        if lbl.get_text().upper() == str(spec.opt("highlight", spec.ticker) or "").upper():
            lbl.set_color(theme.ORANGE_DK); lbl.set_fontweight("bold")
    ax.grid(False)
    for side in ("top", "right", "left", "bottom"):
        ax.spines[side].set_visible(False)
    ax.tick_params(length=0)
    ax.xaxis.set_ticks_position("top")
    ax.xaxis.set_label_position("top")
    chrome.set_top(ax, theme.MARGIN["top"] - 0.045)
    chrome.set_bottom(ax, 0.095)
    return [], []


def _luminance_rgba(rgba) -> float:
    r, g, b = rgba[0], rgba[1], rgba[2]
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


PRIMITIVES = {
    "combo_trend": combo_trend,
    "stacked_bar": stacked_bar,
    "band_history": band_history,
    "surprise": surprise,
    "peer_bar": peer_bar,
    "waterfall": waterfall,
    "scatter_xy": scatter_xy,
    "heatmap": heatmap,
}
