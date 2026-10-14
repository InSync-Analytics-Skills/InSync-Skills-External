"""Public render API.

    from insync_charts import render_chart, render_many
    result = render_chart(spec_dict, out_dir="charts/")
    # -> {"chart_type":..., "files": {"png": "...", "svg": "..."}, "warnings":[...]}
"""
from __future__ import annotations

import json
import os
import traceback
from typing import Any, Dict, List, Optional, Sequence

from . import chrome, observe, primitives, registry, spec as spec_mod, theme
from . import dataquality as dq
from .spec import ChartSpec, SpecError

__all__ = ["render_chart", "render_figure", "render_many", "validate",
           "catalogue", "SpecError"]


def _apply_registry_defaults(raw: Dict[str, Any]) -> Dict[str, Any]:
    meta = registry.resolve(str(raw.get("chart_type", "")))
    raw = dict(raw)
    raw["chart_type"] = meta["key"]
    opts = dict(meta["options"])
    opts.update(raw.get("options") or {})
    raw["options"] = opts
    units = dict(raw.get("units") or {})
    series_unit = next((s.get("unit") for s in (raw.get("series") or [])
                        if isinstance(s, dict) and s.get("unit")
                        and (s.get("axis") or "left") == "left"), None)
    if not units.get("left"):
        units["left"] = series_unit or meta["unit"]
    raw["units"] = units
    raw["_meta"] = meta
    return raw


def _check_requirements(s: ChartSpec, meta: Dict[str, Any]) -> None:
    req = meta["requires"]
    if req == "categories" and not (s.categories or s.periods):
        raise SpecError(f"chart_type '{s.chart_type}' requires 'categories' "
                        f"(the peer tickers / bridge steps)")
    if req == "matrix" and not s.matrix:
        raise SpecError(f"chart_type '{s.chart_type}' requires a 'matrix' plus "
                        f"'row_labels' and 'col_labels'")
    if req == "series" and not s.series:
        raise SpecError(f"chart_type '{s.chart_type}' requires at least one series")


def _auto_subtitle(s: ChartSpec, meta: Dict[str, Any]) -> Optional[str]:
    if s.subtitle is not None:
        return s.subtitle
    bits: List[str] = []
    base = meta.get("subtitle")
    if not any(x.axis == "right" for x in s.series):
        base = meta.get("subtitle_single", base)
    if base:
        bits.append(base)
    sector = s.company.get("sector")
    if sector:
        bits.append(str(sector))
    if s.periods:
        bits.append(f"{s.periods[0]} – {s.periods[-1]}")
    return "  ·  ".join(bits) if bits else None


def validate(raw: Dict[str, Any]) -> Dict[str, Any]:
    try:
        prepared = _apply_registry_defaults(raw)
        s = spec_mod.parse(prepared)
        _check_requirements(s, prepared["_meta"])
        return {"ok": True, "error": None, "warnings": s.warnings,
                "chart_type": s.chart_type,
                "primitive": prepared["_meta"]["primitive"]}
    except (SpecError, KeyError, ValueError) as e:
        return {"ok": False, "error": str(e).strip("'"), "warnings": []}


def render_figure(raw: Dict[str, Any]):
    theme.apply_determinism()
    prepared = _apply_registry_defaults(raw)
    meta = prepared["_meta"]
    s = spec_mod.parse(prepared)
    _check_requirements(s, meta)

    findings = dq.check(s, s.periods or s.categories, meta["primitive"])
    s.warnings.extend(f"[{f['severity']}] {f['code']}"
                      + (f" ({f['series']})" if f.get("series") else "")
                      + f": {f['message']}" for f in findings)

    obs = observe.for_chart(s, meta["primitive"])

    fig, ax = chrome.new_figure(s)
    handles, names = primitives.PRIMITIVES[meta["primitive"]](fig, ax, s)
    chrome.finalize_layout(fig, ax, s, chrome.wants_legend(handles, s),
                           has_observation=bool(obs))
    chrome.heading(fig, s, meta["title"], _auto_subtitle(s, meta))
    chrome.legend(fig, ax, handles, names, s)
    chrome.observation(fig, ax, obs)

    notes = list(dq.disclosure(findings))
    if any(v is None for ser in s.series for v in ser.values):
        notes.append("n/a = not reported")
    chrome.footer(fig, s, notes)
    ax._insync["findings"] = findings
    ax._insync["observation"] = obs
    ax._insync["validation"] = dq.summary(s, s.periods or s.categories,
                                          meta["primitive"], findings)
    return fig, s, meta


def render_chart(raw: Dict[str, Any], out_dir: str = ".",
                 strict: bool = True) -> Dict[str, Any]:
    theme.apply_determinism()
    try:
        backend = str((raw.get("options") or {}).get("backend", "matplotlib")).lower()
        if backend == "plotly":
            prepared = _apply_registry_defaults(raw)
            s0 = spec_mod.parse(prepared)
            from .plotly_backend import try_render as _plotly
            res = _plotly(s0, prepared["_meta"], out_dir)
            if res is not None:
                return res

        fig, s, meta = render_figure(raw)
        if backend == "plotly":
            s.warnings.append("plotly backend unavailable or unsupported for this "
                              "chart type; fell back to matplotlib")
        ax = fig.axes[0] if fig.axes else None
        info = getattr(ax, "_insync", {}) if ax is not None else {}
        findings = info.get("findings", [])
        files = chrome.export(fig, s, out_dir)

        return {"ok": True, "chart_type": s.chart_type, "primitive": meta["primitive"],
                "ticker": s.ticker, "title": s.title or meta["title"],
                "files": files, "warnings": s.warnings, "error": None,
                "observation": info.get("observation", ""),
                "data_quality": dq.worst(findings),
                "findings": findings,
                "validation": info.get("validation", {}),
                "plot_fraction": info.get("plot_fraction")}
    except Exception as e:
        if strict:
            raise
        return {"ok": False, "chart_type": raw.get("chart_type"), "files": {},
                "warnings": [], "error": f"{type(e).__name__}: {e}",
                "traceback": traceback.format_exc()}


def render_many(specs: Sequence[Dict[str, Any]], out_dir: str = ".",
                strict: bool = False) -> Dict[str, Any]:
    results = [render_chart(sp, out_dir, strict=strict) for sp in specs]
    return {
        "count": len(results),
        "rendered": sum(1 for r in results if r.get("ok")),
        "failed": sum(1 for r in results if not r.get("ok")),
        "out_dir": os.path.abspath(out_dir),
        "charts": results,
    }


def catalogue() -> List[Dict[str, str]]:
    return registry.catalogue()


def load_specs(path: str) -> List[Dict[str, Any]]:
    if path == "-":
        import sys
        data = json.load(sys.stdin)
    else:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    if isinstance(data, dict) and "charts" in data:
        shared = {k: v for k, v in data.items() if k != "charts"}
        out = []
        for c in data["charts"]:
            merged = dict(shared)
            merged.update(c)
            out.append(merged)
        return out
    if isinstance(data, dict):
        return [data]
    if isinstance(data, list):
        return list(data)
    raise SpecError("spec file must contain an object or a list of objects")
