"""Optional Plotly backend — same tokens, different renderer.

Matplotlib is the default and the reference. Plotly exists for two reasons:

1. HTML portals (earnings-preview-portal and friends) need an *interactive*
   chart that still matches the static house style. `insync_template()` gives
   them one palette to import rather than re-deriving colours.
2. Static export via Kaleido, when a caller specifically asks for it.

Anything Plotly cannot do in-house-style falls back to matplotlib automatically.
Plotly and Kaleido are never required for the skill to work.
"""
from __future__ import annotations

import os
from typing import Any, Dict, Optional

from . import fmt, theme
from .spec import ChartSpec

SUPPORTED_PRIMITIVES = {"combo_trend", "stacked_bar", "peer_bar"}


def available() -> bool:
    try:
        import plotly
        return True
    except Exception:
        return False


def kaleido_available() -> bool:
    try:
        import plotly.io as pio
        return pio.kaleido.scope is not None
    except Exception:
        return False


def insync_template():
    try:
        import plotly.graph_objects as go
        import plotly.io as pio
    except Exception:
        return None
    t = go.layout.Template()
    t.layout = go.Layout(
        paper_bgcolor=theme.BG, plot_bgcolor=theme.BG,
        colorway=list(theme.SERIES),
        font=dict(family=f"{theme.SANS}, Calibri, Arial, sans-serif",
                  size=theme.FS_TICK + 2, color=theme.INK),
        title=dict(font=dict(family=f"{theme.SERIF}, Georgia, serif",
                             size=int(theme.FS_TITLE) + 4, color=theme.INK),
                   x=0.0, xanchor="left"),
        xaxis=dict(showgrid=False, zeroline=False, linecolor=theme.AXIS,
                   ticks="outside", tickcolor=theme.AXIS,
                   tickfont=dict(color=theme.GREY)),
        yaxis=dict(gridcolor=theme.GRID, zeroline=False, showline=False,
                   ticks="", tickfont=dict(color=theme.GREY)),
        legend=dict(orientation="h", yanchor="bottom", y=-0.22, x=0,
                    bgcolor="rgba(0,0,0,0)", font=dict(size=theme.FS_LEGEND + 2)),
        margin=dict(l=64, r=24, t=68, b=64),
        hoverlabel=dict(bgcolor=theme.BG, bordercolor=theme.HAIRLINE,
                        font=dict(color=theme.INK)),
    )
    pio.templates["insync"] = t
    return t


def try_render(spec: ChartSpec, meta: Dict[str, Any], out_dir: str) -> Optional[Dict[str, Any]]:
    if not available() or meta["primitive"] not in SUPPORTED_PRIMITIVES:
        return None
    try:
        import plotly.graph_objects as go
    except Exception:
        return None

    insync_template()
    labels = spec.periods or spec.categories
    fig = go.Figure()
    for i, s in enumerate(spec.series):
        col = s.color or theme.series_color(i, focus=s.focus)
        if s.kind == "bar":
            fig.add_bar(x=labels, y=s.values, name=s.name, marker_color=col)
        else:
            fig.add_scatter(x=labels, y=s.values, name=s.name, mode="lines+markers",
                            line=dict(color=col, width=2.2),
                            yaxis="y2" if s.axis == "right" else "y")

    if meta["primitive"] == "stacked_bar":
        fig.update_layout(barmode="stack")

    title = spec.title or f"{spec.company_name} ({spec.ticker}) — {meta['title']}"
    single = not any(s.axis == "right" for s in spec.series)
    subtitle = (spec.subtitle or (meta.get("subtitle_single") if single else None)
                or meta.get("subtitle") or "")
    src = f"Source: {spec.source}  |  prepared {spec.report_date}"
    fig.update_layout(
        template="insync",
        title=dict(text=f"{title}<br><span style='font-size:12px;color:{theme.GREY}'>"
                        f"{subtitle}</span>"),
        yaxis_title=fmt.axis_label(spec.units.get("left"), spec.axis_labels.get("left")),
        annotations=[dict(text=src, showarrow=False, xref="paper", yref="paper",
                          x=0, y=-0.30, xanchor="left",
                          font=dict(size=theme.FS_SOURCE + 2, color=theme.GREY_LT)),
                     dict(text=f"<b><span style='color:{theme.ORANGE}'>INSYNC</span> "
                               f"<span style='color:{theme.INK}'>ANALYTICS</span></b>",
                          showarrow=False, xref="paper", yref="paper",
                          x=1, y=-0.30, xanchor="right",
                          font=dict(size=theme.FS_WORDMARK + 2))],
    )
    if any(s.axis == "right" for s in spec.series):
        fig.update_layout(yaxis2=dict(overlaying="y", side="right", showgrid=False,
                                      title=fmt.axis_label(spec.units.get("right"),
                                                           spec.axis_labels.get("right"))))

    p = theme.preset(spec.output.get("preset"))
    w, h, scale = int(p["w"] * 96), int(p["h"] * 96), 2

    os.makedirs(out_dir, exist_ok=True)
    base = spec.output["basename"]
    files: Dict[str, str] = {}
    formats = list(spec.output["formats"])
    if any(f in ("png", "pdf") for f in formats) and not kaleido_available():
        return None
    for ext in formats:
        path = os.path.join(out_dir, f"{base}.{ext}")
        if ext == "svg":
            fig.write_image(path, width=w, height=h) if kaleido_available() else None
            if not os.path.exists(path):
                return None
        else:
            fig.write_image(path, width=w, height=h, scale=scale)
        files[ext] = path
    if spec.opt("emit_html", False):
        hp = os.path.join(out_dir, f"{base}.html")
        fig.write_html(hp, include_plotlyjs="cdn", full_html=True)
        files["html"] = hp

    return {"ok": True, "chart_type": spec.chart_type, "primitive": meta["primitive"],
            "ticker": spec.ticker, "title": title, "files": files,
            "warnings": spec.warnings + ["rendered with the Plotly backend"],
            "error": None}
