"""InSync Analytics static-chart theme.

Single source of truth for palette, typography, geometry and matplotlib rcParams.
Every chart in the InSync skills library renders through these tokens, so the same
chart type always looks identical across skills, sessions and machines.

Design target: institutional equity-research house style.
White background, hairline gridlines, muted palette, no decoration.
"""
from __future__ import annotations

import os
from typing import Dict, List, Optional, Sequence

import matplotlib

matplotlib.use("Agg")

from matplotlib import font_manager as _fm


def _load_brand():
    import json as _json, os as _os
    here = _os.path.dirname(_os.path.abspath(__file__))
    for up in range(0, 6):
        cand = _os.path.normpath(_os.path.join(here, *([".."] * up), "common", "brand.json"))
        if _os.path.exists(cand):
            try:
                with open(cand, "r", encoding="utf-8") as fh:
                    return _json.load(fh)
            except Exception:
                return {}
    return {}


_BRAND = _load_brand()
_BC = _BRAND.get("colors", {})
_BCH = _BRAND.get("charts", {})
_BF = _BRAND.get("fonts", {})
_h = lambda v, d: ("#" + v) if isinstance(v, str) and v else d

INK = _h(_BC.get("ink"), "#1A1A1A")
ORANGE = _h(_BC.get("orange"), "#E8850C")
ORANGE_DK = _h(_BC.get("orangeDk"), "#B25E08")
GREY = _h(_BC.get("grayLight"), "#555555")
GREY_LT = "#9A9A9A"
HAIRLINE = _h(_BCH.get("hairline") or _BC.get("tableBorder"), "#DDDDDD")
GRID = _h(_BCH.get("grid"), "#E6E6E6")
AXIS = _h(_BCH.get("axis"), "#BFBFBF")
BG = _h(_BCH.get("background"), "#FFFFFF")

POS = _h(_BC.get("beatGreen"), "#1E7A34")
NEG = _h(_BC.get("missRed"), "#B02A1F")
NEUTRAL = _h(_BC.get("neutralGrey"), "#8A8A8A")
TOTAL = _h(_BCH.get("total_bar"), "#1F3A5F")

SERIES: List[str] = ([("#" + c) for c in _BCH["series_ramp"]] if _BCH.get("series_ramp") else [
    "#1F3A5F",
    "#E8850C",
    "#7C93A8",
    "#B25E08",
    "#4E6E58",
    "#3E3E3E",
    "#A9B7C4",
])

LINE_STYLES: List[str] = ["-", "--", "-.", ":", (0, (5, 1, 1, 1)), (0, (3, 1, 1, 1, 1, 1)), "-", "--"]
MARKERS: List[str] = ["o", "s", "^", "D", "v", "P", "X", "*"]
HATCHES: List[Optional[str]] = [None, None, "...", "\\\\\\", "xxx", "+++", "ooo", "---"]

STACK_RAMP: List[str] = [
    "#1F3A5F",
    "#3E5F80",
    "#6E8CA8",
    "#9DB2C4",
    "#C6D1DB",
    "#E4E9EE",
]

DIVERGING = ["#9E3232", "#C48A8A", "#EDE7E4", "#FFFFFF", "#E4EBE7", "#8FB2A0", "#2E6B4F"]
SEQUENTIAL = ([("#" + c) for c in _BCH["sequential"]] if _BCH.get("sequential")
              else ["#FFFFFF", "#E4E9EE", "#C3CFDA", "#9BB0C2", "#6E8CA8", "#42648A", "#1F3A5F"])

ESTIMATE_ALPHA = 0.55
ESTIMATE_HATCH = (_BCH.get("estimate_hatch")
                  if isinstance(_BCH.get("estimate_hatch"), str) and _BCH.get("estimate_hatch")
                  else "///")

SERIF_STACK = [_BF.get("title", "Georgia"), "Caladea", "Liberation Serif", "Nimbus Roman", "DejaVu Serif"]
SANS_STACK = [_BF.get("body", "Calibri"), "Carlito", "Liberation Sans", "Nimbus Sans", "DejaVu Sans"]


def _resolve(stack: Sequence[str]) -> str:
    available = {f.name for f in _fm.fontManager.ttflist}
    for name in stack:
        if name in available:
            return name
    return stack[-1]


SERIF = _resolve(SERIF_STACK)
SANS = _resolve(SANS_STACK)

BASE_FS = {
    "title": 12.5, "subtitle": 8.8, "axis_label": 8.0, "tick": 8.0,
    "legend": 7.8, "datalabel": 7.4, "source": 6.0, "wordmark": 6.4,
    "observation": 8.0,
}
FS_TITLE = BASE_FS["title"]
FS_SUBTITLE = BASE_FS["subtitle"]
FS_AXIS_LABEL = BASE_FS["axis_label"]
FS_TICK = BASE_FS["tick"]
FS_LEGEND = BASE_FS["legend"]
FS_DATALABEL = BASE_FS["datalabel"]
FS_SOURCE = BASE_FS["source"]
FS_WORDMARK = BASE_FS["wordmark"]
FS_OBSERVATION = BASE_FS["observation"]
SCALE = 1.0
REFERENCE_WIDTH_IN = 6.5


def set_scale(width_in: float) -> float:
    global SCALE, FS_TITLE, FS_SUBTITLE, FS_AXIS_LABEL, FS_TICK
    global FS_LEGEND, FS_DATALABEL, FS_SOURCE, FS_WORDMARK, FS_OBSERVATION
    raw = width_in / REFERENCE_WIDTH_IN
    SCALE = round(min(1.30, max(0.66, raw ** 0.55)), 4)
    FS_TITLE = round(BASE_FS["title"] * SCALE, 2)
    FS_SUBTITLE = round(BASE_FS["subtitle"] * SCALE, 2)
    FS_AXIS_LABEL = round(BASE_FS["axis_label"] * SCALE, 2)
    FS_TICK = round(BASE_FS["tick"] * SCALE, 2)
    FS_LEGEND = round(BASE_FS["legend"] * SCALE, 2)
    FS_DATALABEL = round(BASE_FS["datalabel"] * SCALE, 2)
    FS_SOURCE = round(BASE_FS["source"] * SCALE, 2)
    FS_WORDMARK = round(BASE_FS["wordmark"] * SCALE, 2)
    FS_OBSERVATION = round(BASE_FS["observation"] * SCALE, 2)
    return SCALE

PRESETS: Dict[str, Dict[str, float]] = {
    "word": {"w": 6.5, "h": 3.7, "dpi": 300},
    "word_wide": {"w": 7.5, "h": 3.9, "dpi": 300},
    "word_half": {"w": 3.2, "h": 2.5, "dpi": 300},
    "word_tall": {"w": 6.5, "h": 5.2, "dpi": 300},
    "slide": {"w": 9.0, "h": 5.0, "dpi": 220},
    "slide_half": {"w": 5.8, "h": 4.2, "dpi": 220},
    "slide_full": {"w": 12.0, "h": 6.3, "dpi": 220},
    "web": {"w": 8.0, "h": 4.5, "dpi": 150},
    "web_wide": {"w": 10.0, "h": 4.2, "dpi": 150},
    "thumb": {"w": 2.6, "h": 1.8, "dpi": 200},
}
DEFAULT_PRESET = "word"

MARGIN = {"left": 0.092, "right": 0.975, "top": 0.845, "bottom": 0.118}
RIGHT_AXIS_MARGIN = 0.912
LEGEND_BAND = 0.052
OBSERVATION_BAND = 0.050
ROTATED_TICK_BAND = 0.052
FOOTNOTE_LINE = 0.030
FOOTNOTE_INLINE_MAX = 58
TITLE_Y = 0.978
SUBTITLE_Y = 0.922
RULE_Y = 0.887
LEGEND_Y = 0.070
OBSERVATION_Y = 0.052
SOURCE_Y = 0.016
RULE_LW = 1.6

LOGO_HEIGHT_RATIO = 1.45
LOGO_MAX_HEIGHT = 0.075
LOGO_BASELINE_DROP = 0.004
LOGO_GAP = 0.010
LOGO_FILE = _BRAND.get("masthead", {}).get("logo_file", "insync_logo.png")

_MH = _BRAND.get("masthead", {})
BRAND_PT = _MH.get("brand_size_half_points", 33) / 2.0
COMPANY_PT = (_MH.get("company_size_ladder_half_points", [28])[0]) / 2.0
MARK_W_PT = _MH.get("mark_px", {}).get("w", 59) * 0.75
MARK_H_PT = _MH.get("mark_px", {}).get("h", 46) * 0.75

MIN_TITLE_SCALE = 0.70


def rc_params(dpi: float) -> Dict[str, object]:
    return {
        "figure.dpi": dpi,
        "savefig.dpi": dpi,
        "figure.facecolor": BG,
        "savefig.facecolor": BG,
        "savefig.edgecolor": BG,
        "axes.facecolor": BG,
        "font.family": "sans-serif",
        "font.sans-serif": [SANS] + SANS_STACK,
        "font.serif": [SERIF] + SERIF_STACK,
        "font.size": FS_TICK,
        "text.color": INK,
        "text.parse_math": False,
        "axes.edgecolor": AXIS,
        "axes.linewidth": 0.7,
        "axes.labelcolor": GREY,
        "axes.labelsize": FS_AXIS_LABEL,
        "axes.titlesize": FS_TITLE,
        "axes.grid": True,
        "axes.grid.axis": "y",
        "axes.axisbelow": True,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "grid.color": GRID,
        "grid.linewidth": 0.6,
        "grid.linestyle": "-",
        "xtick.color": GREY,
        "ytick.color": GREY,
        "xtick.labelsize": FS_TICK,
        "ytick.labelsize": FS_TICK,
        "xtick.direction": "out",
        "ytick.direction": "out",
        "xtick.major.size": 2.5,
        "ytick.major.size": 0.0,
        "xtick.major.width": 0.7,
        "legend.frameon": False,
        "legend.fontsize": FS_LEGEND,
        "legend.handlelength": 1.9,
        "legend.handletextpad": 0.55,
        "legend.columnspacing": 1.5,
        "legend.labelspacing": 0.35,
        "lines.linewidth": 1.7,
        "lines.markersize": 4.0,
        "lines.solid_capstyle": "round",
        "patch.linewidth": 0.0,
        "hatch.linewidth": 0.6,
        "hatch.color": BG,
        "path.simplify": True,
        "svg.hashsalt": "insync-static-charts",
        "svg.fonttype": "path",
        "pdf.fonttype": 42,
        "ps.fonttype": 42,
    }


def apply_determinism() -> None:
    os.environ.setdefault("SOURCE_DATE_EPOCH", "1451606400")
    os.environ.setdefault("PYTHONHASHSEED", "0")


def series_color(i: int, focus: bool = False, reserve_brand: bool = False) -> str:
    if focus:
        return ORANGE
    pool = [c for c in SERIES if c not in (ORANGE, ORANGE_DK)] if reserve_brand else SERIES
    return pool[i % len(pool)]


def stack_color(i: int, n: int) -> str:
    if n <= 1:
        return STACK_RAMP[0]
    if n <= len(STACK_RAMP):
        step = (len(STACK_RAMP) - 1) / (n - 1)
        return STACK_RAMP[int(round(i * step))]
    return STACK_RAMP[i % len(STACK_RAMP)]


def series_style(i: int) -> str:
    return LINE_STYLES[i % len(LINE_STYLES)]


def series_marker(i: int) -> str:
    return MARKERS[i % len(MARKERS)]


def series_hatch(i: int) -> Optional[str]:
    return HATCHES[i % len(HATCHES)]


def preset(name: Optional[str]) -> Dict[str, float]:
    return dict(PRESETS.get(name or DEFAULT_PRESET, PRESETS[DEFAULT_PRESET]))


def token_map() -> Dict[str, object]:
    return {
        "ink": INK, "orange": ORANGE, "orange_dark": ORANGE_DK, "grey": GREY,
        "grey_light": GREY_LT, "hairline": HAIRLINE, "grid": GRID, "axis": AXIS,
        "background": BG, "positive": POS, "negative": NEG, "neutral": NEUTRAL,
        "total": TOTAL, "series": list(SERIES), "stack_ramp": list(STACK_RAMP),
        "diverging": list(DIVERGING),
        "sequential": list(SEQUENTIAL), "serif": SERIF, "sans": SANS,
    }
