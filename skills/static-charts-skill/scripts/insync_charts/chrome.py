"""Standard chart chrome: the frame every InSync chart wears.

Title, subtitle, 2pt orange rule, axis labels, legend, source line, brand
wordmark, and deterministic export. No chart type is allowed to draw its own
title or source line — they all come through here, so they are always identical.
"""
from __future__ import annotations

import textwrap
import os
import warnings
from typing import Dict, List, Optional, Tuple

import matplotlib.pyplot as plt
from matplotlib.figure import Figure
from matplotlib.lines import Line2D
from matplotlib.patches import Patch, Rectangle

from . import fmt, theme
from .spec import ChartSpec


def new_figure(spec: ChartSpec) -> Tuple[Figure, plt.Axes]:
    p = theme.preset(spec.output.get("preset"))
    w = float(spec.output.get("width_in") or p["w"])
    h = float(spec.output.get("height_in") or p["h"])
    dpi = float(spec.output.get("dpi") or p["dpi"])
    theme.set_scale(w)
    plt.rcParams.update(theme.rc_params(dpi))
    fig = plt.figure(figsize=(w, h), dpi=dpi)
    ax = fig.add_axes(_rect(theme.MARGIN["bottom"], theme.MARGIN["right"],
                            theme.MARGIN["top"]))
    ax.set_facecolor(theme.BG)
    ax._insync = {"w": w, "h": h, "ax2": None, "top": theme.MARGIN["top"]}
    return fig, ax


def _rect(bottom: float, right: float, top: float) -> list:
    return [theme.MARGIN["left"], bottom, right - theme.MARGIN["left"], top - bottom]


def register_secondary(ax: plt.Axes, ax2: Optional[plt.Axes]) -> None:
    ax._insync["ax2"] = ax2


def set_top(ax: plt.Axes, top: float) -> None:
    ax._insync["top"] = top


def set_bottom(ax: plt.Axes, bottom: float) -> None:
    ax._insync["bottom"] = bottom


def finalize_layout(fig: Figure, ax: plt.Axes, spec: ChartSpec,
                    has_legend: bool, has_observation: bool = False) -> None:
    info = getattr(ax, "_insync", {})
    ax2 = info.get("ax2")
    top = info.get("top", theme.MARGIN["top"])
    h = float(info.get("h", 3.7))

    right = theme.RIGHT_AXIS_MARGIN if ax2 is not None else theme.MARGIN["right"]
    bottom = info.get("bottom", theme.MARGIN["bottom"])

    fn_lines = footnote_lines(fig, spec)
    fn_band = len(fn_lines) * theme.FOOTNOTE_LINE

    rotated = any(abs(t.get_rotation()) > 1 for t in ax.get_xticklabels())
    k = 3.7 / h
    bottom += fn_band * k
    if rotated:
        bottom += theme.ROTATED_TICK_BAND * k
    if has_observation:
        bottom += theme.OBSERVATION_BAND * k
    if has_legend:
        bottom += theme.LEGEND_BAND * k
    if ax.get_xlabel():
        bottom += 0.042 * k

    bottom = min(bottom, top - 0.28)
    ax.set_position(_rect(bottom, right, top))
    if ax2 is not None:
        ax2.set_position(ax.get_position())
    fn_top = fn_band * k
    fig._insync_footnote = {"lines": fn_lines, "y": theme.SOURCE_Y + 0.026 * k}
    obs_y = (theme.OBSERVATION_Y * k + fn_top) if has_observation else None
    ax._insync["observation_y"] = obs_y
    ax._insync["legend_y"] = ((obs_y + theme.OBSERVATION_BAND * k * 0.62)
                              if obs_y else theme.SOURCE_Y + 0.040 * k + fn_top)
    ax._insync["plot_fraction"] = round((top - bottom) * (right - theme.MARGIN["left"]), 4)


def grid(ax: plt.Axes, axis: str = "y") -> None:
    ax.grid(False)
    ax.grid(True, axis=axis, color=theme.GRID, linewidth=0.6, zorder=0)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    ax.spines["left"].set_visible(False)
    ax.spines["bottom"].set_color(theme.AXIS)
    ax.spines["bottom"].set_linewidth(0.7)
    ax.tick_params(axis="y", length=0)
    ax.tick_params(axis="x", length=2.5, color=theme.AXIS)


def zero_line(ax: plt.Axes) -> None:
    lo, hi = ax.get_ylim()
    if lo < 0 < hi:
        ax.axhline(0, color=theme.AXIS, linewidth=0.9, zorder=2)


def secondary_axis(ax: plt.Axes) -> plt.Axes:
    ax2 = ax.twinx()
    ax2.grid(False)
    for side in ("top", "left"):
        ax2.spines[side].set_visible(False)
    ax2.spines["right"].set_visible(False)
    ax2.tick_params(axis="y", length=0, colors=theme.GREY, labelsize=theme.FS_TICK)
    ax2.set_facecolor("none")
    return ax2


def _default_title(spec: ChartSpec, fallback: str) -> str:
    name = spec.company_name
    tick = spec.ticker
    head = f"{name} ({tick})" if name and tick and name.upper() != tick else (name or tick)
    return f"{head} — {fallback}" if head else fallback


def heading(fig: Figure, spec: ChartSpec, default_title: str,
            default_subtitle: Optional[str] = None) -> None:
    title = spec.title or _default_title(spec, default_title)
    subtitle = spec.subtitle if spec.subtitle is not None else default_subtitle

    content_w = theme.MARGIN["right"] - theme.MARGIN["left"]
    t = fig.text(theme.MARGIN["left"], theme.TITLE_Y, title,
                 ha="left", va="top", color=theme.INK,
                 fontfamily=theme.SERIF, fontsize=theme.FS_TITLE, fontweight="bold")
    _shrink_to_fit(fig, t, content_w, theme.FS_TITLE)
    _shorten_to_fit(fig, t, content_w, spec, "title")

    if subtitle:
        s = fig.text(theme.MARGIN["left"], theme.SUBTITLE_Y, subtitle,
                     ha="left", va="top", color=theme.GREY,
                     fontfamily=theme.SANS, fontsize=theme.FS_SUBTITLE)
        _shrink_to_fit(fig, s, content_w, theme.FS_SUBTITLE)
        _shorten_to_fit(fig, s, content_w, spec, "subtitle")

    fig.add_artist(Line2D(
        [theme.MARGIN["left"], theme.MARGIN["right"]],
        [theme.RULE_Y, theme.RULE_Y],
        transform=fig.transFigure, color=theme.ORANGE,
        linewidth=theme.RULE_LW, solid_capstyle="butt", zorder=5,
    ))


def _shrink_to_fit(fig: Figure, artist, max_w: float, base_size: float) -> None:
    fig.canvas.draw()
    size = base_size
    floor = base_size * theme.MIN_TITLE_SCALE
    while _text_width(fig, artist) > max_w and size > floor:
        size = max(floor, size - 0.4)
        artist.set_fontsize(size)


def _shorten_to_fit(fig: Figure, artist, max_w: float, spec: ChartSpec, what: str) -> None:
    if _text_width(fig, artist) <= max_w:
        return
    full = artist.get_text()
    words = full.split(" ")
    while len(words) > 1 and _text_width(fig, artist) > max_w:
        words.pop()
        artist.set_text(" ".join(words).rstrip(" —–-:,") + "…")
    text = artist.get_text()
    while len(text) > 2 and _text_width(fig, artist) > max_w:
        text = text[:-2] + "…"
        artist.set_text(text)
    spec.warnings.append(f"{what} too long for the '{spec.output.get('preset')}' size and "
                         f"was shortened to '{artist.get_text()}'; set a shorter "
                         f"'{what}' (full text: '{full}')")


def footer(fig: Figure, spec: ChartSpec, extra_notes: Optional[List[str]] = None) -> None:
    core = f"Source: {spec.source}"
    optional: List[str] = [f"prepared {spec.report_date}"]
    if spec.as_of:
        optional.insert(0, f"data as of {spec.as_of}")
    optional.extend([n for n in (extra_notes or []) if n])

    if spec.opt("wordmark", True):
        wm_right = _wordmark(fig, with_logo=bool(spec.opt("logo", True)))
    else:
        wm_right = theme.MARGIN["right"]
    budget = max(0.05, wm_right - theme.MARGIN["left"] - 0.015)

    block = getattr(fig, "_insync_footnote", None) or {}
    lines = block.get("lines") or []
    note = spec.opt("footnote")
    note = str(note).strip() if note else ""
    inline = optional + ([note] if note and not lines else [])

    txt = fig.text(theme.MARGIN["left"], theme.SOURCE_Y,
                   "  |  ".join([core] + inline),
                   ha="left", va="bottom", color=theme.GREY_LT,
                   fontfamily=theme.SANS, fontsize=theme.FS_SOURCE)
    while inline and _text_width(fig, txt) > budget:
        inline.pop(0) if len(inline) > 1 else inline.pop()
        txt.set_text("  |  ".join([core] + inline))
    if _text_width(fig, txt) > budget:
        txt.set_fontsize(theme.FS_SOURCE * 0.85)

    if lines:
        fig.text(theme.MARGIN["left"], block.get("y", theme.SOURCE_Y + 0.026),
                 "\n".join(lines), ha="left", va="bottom", color=theme.GREY_LT,
                 fontfamily=theme.SANS, fontsize=theme.FS_SOURCE, linespacing=1.35)


def footnote_lines(fig: Figure, spec: ChartSpec) -> List[str]:
    note = spec.opt("footnote")
    note = str(note).strip() if note else ""
    if not note or len(note) <= theme.FOOTNOTE_INLINE_MAX:
        return []
    budget = theme.MARGIN["right"] - theme.MARGIN["left"]
    ref = "n" * 80
    probe = fig.text(0, -1, ref, fontfamily=theme.SANS, fontsize=theme.FS_SOURCE)
    per_char = (_text_width(fig, probe) or 0.4) / len(ref)
    probe.remove()
    ncols = max(24, int(budget / per_char)) if per_char > 0 else 120
    return textwrap.wrap(note, width=ncols) or [note]


def observation(fig: Figure, ax: plt.Axes, text: str) -> None:
    if not text:
        return
    y = getattr(ax, "_insync", {}).get("observation_y") or theme.OBSERVATION_Y
    max_w = theme.MARGIN["right"] - theme.MARGIN["left"]
    t = fig.text(theme.MARGIN["left"], y, text, ha="left", va="bottom",
                 color=theme.INK, fontfamily=theme.SANS,
                 fontsize=theme.FS_OBSERVATION)
    size = theme.FS_OBSERVATION
    floor = size * 0.78
    fig.canvas.draw()
    while _text_width(fig, t) > max_w and size > floor:
        size = max(floor, size - 0.3)
        t.set_fontsize(size)
    if _text_width(fig, t) > max_w:
        body = text
        while body and _text_width(fig, t) > max_w:
            body = body.rsplit(";", 1)[0] if ";" in body else body[:-4]
            t.set_text(body.rstrip(" ;") + "…")


def _text_width(fig: Figure, artist) -> float:
    try:
        bb = artist.get_window_extent(renderer=fig.canvas.get_renderer())
        return bb.transformed(fig.transFigure.inverted()).width
    except Exception:
        return 0.0


def _logo_path() -> Optional[str]:
    p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     os.pardir, "assets", theme.LOGO_FILE)
    p = os.path.normpath(p)
    return p if os.path.exists(p) else None


def _run(fig: Figure, x: float, y: float, parts, va: str = "top") -> float:
    fig.canvas.draw()
    for text, color, weight, family, size in parts:
        t = fig.text(x, y, text, ha="left", va=va, color=color,
                     fontfamily=family, fontsize=size, fontweight=weight)
        x += _text_width(fig, t)
    return x


def _run_right(fig: Figure, x_right: float, y: float, parts,
               va: str = "top") -> float:
    fig.canvas.draw()
    total = 0.0
    probes = []
    for text, color, weight, family, size in parts:
        p = fig.text(-5, -5, text, fontfamily=family, fontsize=size,
                     fontweight=weight)
        total += _text_width(fig, p)
        probes.append(p)
    for p in probes:
        p.remove()
    _run(fig, x_right - total, y, parts, va=va)
    return x_right - total


def masthead(fig: Figure, *, product: str, company: str = "", ticker: str = "",
             period: str = "", rows=(), y_top: float = 0.965,
             height: float = 0.15, box: bool = True, scale: float = 1.0) -> float:
    from matplotlib.patches import Rectangle

    L, R = theme.MARGIN["left"], theme.MARGIN["right"]
    pad_x, pad_y = 0.016, 0.022
    y_bot = y_top - height

    if box:
        fig.add_artist(Rectangle((L, y_bot), R - L, height,
                                 transform=fig.transFigure, facecolor="none",
                                 edgecolor="#8A8A8A", linewidth=0.8, zorder=3))

    fs_brand = theme.BRAND_PT
    fs_company = theme.COMPANY_PT
    fs_product = theme.FS_TITLE * 1.55 * scale
    fs_meta = theme.FS_SUBTITLE * 0.95 * scale
    fig_h = max(fig.get_figheight(), 1e-6)

    def pt_frac(pts: float) -> float:
        return pts / 72.0 / fig_h

    line = []
    if company:
        line.append((company, theme.INK, "bold", theme.SERIF, fs_company))
    if ticker:
        line.append((f"  ({ticker})", theme.ORANGE, "bold", theme.SERIF, fs_company))
    if period:
        line.append((f" - {period}", theme.INK, "bold", theme.SERIF, fs_company))

    lead = pt_frac(4.5)
    brand_h, comp_h = pt_frac(fs_brand), pt_frac(fs_company)
    left_h = brand_h + ((lead + comp_h) if line else 0.0)

    live_rows = [(l, v) for l, v in rows if v]
    meta_step = pt_frac(fs_meta) * 1.40
    right_h = pt_frac(fs_product) + (pt_frac(3.0) + len(live_rows) * meta_step
                                     if live_rows else 0.0)

    def block_top(h: float) -> float:
        return y_bot + (height + min(h, height - 2 * pad_y * 0.5)) / 2.0

    left_top = block_top(left_h)
    logo_right = _logo_left_of(fig, L + pad_x, y_bot, height, block_h=left_h)
    _run(fig, logo_right, left_top, [
        ("InSync", theme.ORANGE, "bold", theme.SANS, fs_brand),
        (" Analytics", theme.INK, "normal", theme.SANS, fs_brand)])
    if line:
        _run(fig, logo_right, left_top - brand_h - lead, line)

    right_top = block_top(right_h)
    _run_right(fig, R - pad_x, right_top, [
        (product, theme.INK, "bold", theme.SERIF, fs_product)])
    y = right_top - pt_frac(fs_product) - pt_frac(3.0)
    for label, value in live_rows:
        _run_right(fig, R - pad_x, y, [
            (f"{label}: ", theme.ORANGE, "bold", theme.SANS, fs_meta),
            (str(value), theme.GREY, "normal", theme.SANS, fs_meta)], va="top")
        y -= meta_step

    rule_y = y_bot - 0.020
    fig.add_artist(Line2D([L, R], [rule_y, rule_y], transform=fig.transFigure,
                          color=theme.ORANGE, linewidth=theme.RULE_LW,
                          solid_capstyle="butt", zorder=5))
    return rule_y


def _logo_left_of(fig: Figure, x_left: float, y_bot: float, band_h: float,
                  block_h: Optional[float] = None) -> float:
    path = _logo_path()
    gap = 0.011
    if path is None:
        return x_left
    try:
        import matplotlib.image as mpimg
        from matplotlib.offsetbox import AnnotationBbox, OffsetImage
        arr = mpimg.imread(path)
    except Exception:
        return x_left

    fig_w_px = fig.get_figwidth() * fig.dpi
    fig_h_px = fig.get_figheight() * fig.dpi
    h_frac = theme.MARK_H_PT / 72.0 / max(fig.get_figheight(), 1e-6)
    h_frac = min(h_frac, band_h * 0.74)
    target_px = h_frac * fig_h_px
    native_px = float(arr.shape[0]) or 1.0
    zoom = (target_px / native_px) * (72.0 / fig.dpi)
    box = OffsetImage(arr, zoom=zoom, resample=True, interpolation="antialiased")
    fig.add_artist(AnnotationBbox(box, (x_left, y_bot + band_h / 2.0),
                                  xycoords=fig.transFigure,
                                  box_alignment=(0.0, 0.5), frameon=False,
                                  pad=0.0, annotation_clip=False, zorder=6))
    return x_left + (target_px * (arr.shape[1] / native_px)) / fig_w_px + gap


def trust_mark(fig: Figure, y: Optional[float] = None, scale: float = 1.0) -> float:
    return _wordmark(fig, with_logo=True, y=y, scale=scale)


def _wordmark(fig: Figure, with_logo: bool = True,
              y: Optional[float] = None, scale: float = 1.0) -> float:
    fig.canvas.draw()
    yy = theme.SOURCE_Y if y is None else y
    fs = theme.FS_WORDMARK * scale
    t = fig.text(theme.MARGIN["right"], yy, "ANALYTICS",
                 ha="right", va="bottom", color=theme.INK,
                 fontfamily=theme.SANS, fontsize=fs, fontweight="bold")
    w1 = _text_width(fig, t) or 0.10
    x = theme.MARGIN["right"] - w1
    t2 = fig.text(x - 0.006, yy, "INSYNC",
                  ha="right", va="bottom", color=theme.ORANGE,
                  fontfamily=theme.SANS, fontsize=fs, fontweight="bold")
    left = x - 0.006 - (_text_width(fig, t2) or 0.07)
    if not with_logo:
        return left

    logo_left = _logo(fig, left - theme.LOGO_GAP, _text_height(fig, t), y=yy)
    return logo_left if logo_left is not None else left


def _logo(fig: Figure, right_x: float, wordmark_h: float,
          y: Optional[float] = None) -> Optional[float]:
    path = _logo_path()
    if path is None:
        return None
    try:
        import matplotlib.image as mpimg
        from matplotlib.offsetbox import AnnotationBbox, OffsetImage
        arr = mpimg.imread(path)
    except Exception:
        return None

    fig_w_px = fig.get_figwidth() * fig.dpi
    fig_h_px = fig.get_figheight() * fig.dpi
    h_frac = min(theme.LOGO_HEIGHT_RATIO * wordmark_h, theme.LOGO_MAX_HEIGHT)
    target_px = h_frac * fig_h_px
    native_px = float(arr.shape[0]) or 1.0
    width_frac = (target_px * (arr.shape[1] / native_px)) / fig_w_px
    if right_x - width_frac < theme.MARGIN["left"] + 0.02 or target_px < 8:
        return None

    zoom = (target_px / native_px) * (72.0 / fig.dpi)
    box = OffsetImage(arr, zoom=zoom, resample=True, interpolation="antialiased")
    base_y = (theme.SOURCE_Y if y is None else y) - theme.LOGO_BASELINE_DROP
    fig.add_artist(AnnotationBbox(
        box, (right_x, base_y),
        xycoords=fig.transFigure, box_alignment=(1.0, 0.0), frameon=False,
        pad=0.0, annotation_clip=False, zorder=6))
    return right_x - width_frac


def _text_height(fig: Figure, artist) -> float:
    try:
        bb = artist.get_window_extent(renderer=fig.canvas.get_renderer())
        return bb.transformed(fig.transFigure.inverted()).height
    except Exception:
        return 0.02


def _axis_peak(ax: plt.Axes) -> float:
    try:
        return max(abs(v) for v in ax.get_ylim())
    except Exception:
        return 0.0


def _rescale_ticks(ax: plt.Axes) -> None:
    f = ax.yaxis.get_major_formatter()
    cfg = getattr(f, "_insync", None)
    if not cfg:
        return
    try:
        peak = max(abs(v) for v in ax.get_ylim())
    except Exception:
        return
    ax.yaxis.set_major_formatter(
        fmt.axis_formatter(cfg["unit"], compact=cfg["compact"], dp=cfg["dp"], peak=peak))


def axis_titles(ax: plt.Axes, spec: ChartSpec, ax2: Optional[plt.Axes] = None) -> None:
    _rescale_ticks(ax)
    if ax2 is not None:
        _rescale_ticks(ax2)
    left = fmt.axis_label(spec.units.get("left"), spec.axis_labels.get("left"),
                          peak=_axis_peak(ax))
    if left:
        ax.set_ylabel(left, color=theme.GREY, fontsize=theme.FS_AXIS_LABEL, labelpad=6)
    xl = spec.axis_labels.get("x")
    if xl:
        ax.set_xlabel(xl, color=theme.GREY, fontsize=theme.FS_AXIS_LABEL, labelpad=6)
    if ax2 is not None:
        right = fmt.axis_label(spec.units.get("right"), spec.axis_labels.get("right"),
                               peak=_axis_peak(ax2))
        if right:
            ax2.set_ylabel(right, color=theme.GREY, fontsize=theme.FS_AXIS_LABEL,
                           labelpad=8, rotation=270, va="bottom")


def wants_legend(handles: List, spec: ChartSpec) -> bool:
    mode = spec.opt("legend", "auto")
    if mode is False or not handles:
        return False
    if mode == "auto":
        return len(handles) >= 2
    return True


def legend(fig: Figure, ax: plt.Axes, handles: List, labels: List[str],
           spec: ChartSpec, ncol: Optional[int] = None) -> None:
    if not wants_legend(handles, spec):
        return
    y = getattr(ax, "_insync", {}).get("legend_y", theme.LEGEND_Y)
    n = ncol or min(len(handles), 5)
    fig.legend(handles, labels, loc="lower left",
               bbox_to_anchor=(theme.MARGIN["left"], y),
               ncol=n, frameon=False, fontsize=theme.FS_LEGEND,
               handlelength=1.9, handletextpad=0.55, columnspacing=1.6,
               labelcolor=theme.INK)


def legend_patch(color: str, hatch: Optional[str] = None, alpha: float = 1.0) -> Patch:
    return Patch(facecolor=color, edgecolor="none", hatch=hatch, alpha=alpha)


def legend_line(color: str, ls: str = "-", marker: Optional[str] = None) -> Line2D:
    return Line2D([0], [0], color=color, linestyle=ls, marker=marker,
                  linewidth=1.7, markersize=4.0)


def data_label(ax: plt.Axes, x: float, y: float, text: str, *,
               above: bool = True, color: str = theme.INK,
               size: float = theme.FS_DATALABEL, rotation: float = 0.0,
               offset: float = 3.0, halo: bool = True) -> None:
    t = ax.annotate(text, (x, y), textcoords="offset points",
                    xytext=(0, offset if above else -offset - 4),
                    ha="center", va="bottom" if above else "top",
                    fontsize=size, color=color, fontfamily=theme.SANS,
                    rotation=rotation, clip_on=False, zorder=6)
    if halo:
        import matplotlib.patheffects as pe
        t.set_path_effects([pe.withStroke(linewidth=1.8, foreground=theme.BG)])


def na_marker(ax: plt.Axes, x: float, baseline: float = 0.0,
              ha: str = "center", dx: float = 0.0) -> None:
    ax.annotate(fmt.NA_TEXT, (x, baseline), textcoords="offset points",
                xytext=(dx, 4 if ha == "center" else 0), ha=ha,
                va="bottom" if ha == "center" else "center",
                fontsize=theme.FS_DATALABEL - 0.6, color=theme.GREY_LT,
                fontfamily=theme.SANS, style="italic", clip_on=False, zorder=6)


def estimate_shading(ax: plt.Axes, start_idx: int, n: int, label: str = "Estimates") -> None:
    if start_idx is None or start_idx >= n:
        return
    ax.axvspan(start_idx - 0.5, n - 0.5, color="#F2F4F6", zorder=0.5, linewidth=0)
    ax.axvline(start_idx - 0.5, color=theme.HAIRLINE, linewidth=0.8, zorder=1)
    ax.annotate(label, xy=((start_idx - 0.5 + n - 0.5) / 2, 1.0),
                xycoords=("data", "axes fraction"), xytext=(0, -9),
                textcoords="offset points", ha="center", va="top",
                fontsize=theme.FS_SOURCE, color=theme.GREY_LT,
                fontfamily=theme.SANS, style="italic")


def category_ticks(ax: plt.Axes, labels: List[str], max_ticks: int = 16) -> None:
    n = len(labels)
    ax.set_xlim(-0.6, n - 0.4)
    step = 1 if n <= max_ticks else -(-n // max_ticks)
    idx = list(range(0, n, step))
    if n - 1 not in idx:
        idx.append(n - 1)
    ax.set_xticks(idx)
    ax.set_xticklabels([labels[i] for i in idx], fontsize=theme.FS_TICK, color=theme.GREY)
    longest = max((len(labels[i]) for i in idx), default=0)
    if longest > 7 and n > 8:
        for lbl in ax.get_xticklabels():
            lbl.set_rotation(45)
            lbl.set_ha("right")


def headroom(ax: plt.Axes, top: float = 0.14, bottom: float = 0.06) -> None:
    lo, hi = ax.get_ylim()
    span = (hi - lo) or 1.0
    ax.set_ylim(lo - span * bottom if lo < 0 else lo, hi + span * top)


def export(fig: Figure, spec: ChartSpec, out_dir: str) -> Dict[str, str]:
    os.makedirs(out_dir, exist_ok=True)
    base = str(spec.output["basename"])
    subdirs = bool(spec.output.get("format_subdirs", False))
    paths: Dict[str, str] = {}
    for ext in spec.output["formats"]:
        path = (os.path.join(out_dir, ext, f"{base}.{ext}") if subdirs
                else os.path.join(out_dir, f"{base}.{ext}"))
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        if ext == "png":
            meta = {"Software": None, "Creation Time": None}
        elif ext == "svg":
            meta = {"Date": None, "Creator": None}
        else:
            meta = {"CreationDate": None, "Producer": None, "Creator": None}
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", message=".*timestamp seems very low.*")
            fig.savefig(path, format=ext, facecolor=theme.BG, edgecolor="none",
                        metadata=meta)
        paths[ext] = path
    plt.close(fig)
    return paths
