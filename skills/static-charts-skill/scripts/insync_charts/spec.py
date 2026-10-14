"""Chart spec: the contract between calling skills and the chart engine.

A spec is plain JSON. It is validated and normalised here before any drawing
happens, so a malformed request fails loudly with a precise message rather than
producing a quietly wrong chart.
"""
from __future__ import annotations

import copy
import datetime as _dt
import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from . import fmt, theme


SOURCE_NOT_STATED = "Source not stated"


def _clean_values(values: List[Any], unit: Optional[str], where: str,
                  warnings: List[str]) -> List[Optional[float]]:
    out = fmt.clean(values)
    bad = fmt.invalid_values(values)
    if bad:
        warnings.append(f"{where}: {', '.join(bad[:4])}{' …' if len(bad) > 4 else ''} "
                        f"{'is' if len(bad) == 1 else 'are'} not a number; drawn as n/a")
    mag = fmt.UNITS[unit]["mag"] if unit in fmt.UNITS else 1.0
    if mag != 1.0:
        converted = False
        for i, v in enumerate(values):
            m = fmt.magnitude_of(v)
            if out[i] is not None and m != 1.0:
                out[i] = out[i] / mag
                converted = True
        if converted:
            warnings.append(f"{where}: values written with B/M/K were converted to "
                            f"{fmt.UNITS[unit]['label']}")
    return out


class SpecError(ValueError):
    pass


VALID_KINDS = {"bar", "line", "area", "scatter", "step", "marker"}
VALID_AXES = {"left", "right"}
VALID_FORMATS = {"png", "svg", "pdf"}
VALID_MISSING = {"gap", "zero", "interpolate"}


@dataclass
class Series:
    name: str
    values: List[Optional[float]]
    kind: str = "bar"
    axis: str = "left"
    unit: Optional[str] = None
    color: Optional[str] = None
    focus: bool = False
    estimate_from: Optional[int] = None
    labels: Optional[List[str]] = None
    show_labels: Optional[bool] = None
    alpha: Optional[float] = None
    linewidth: Optional[float] = None
    zorder: Optional[int] = None
    stack_group: Optional[str] = None
    derive: Optional[Dict[str, Any]] = None
    prior: List[Optional[float]] = field(default_factory=list)
    meta: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ChartSpec:
    chart_type: str
    raw: Dict[str, Any]
    company: Dict[str, Any]
    title: Optional[str]
    subtitle: Optional[str]
    source: str
    report_date: str
    as_of: Optional[str]
    periods: List[str]
    series: List[Series]
    categories: List[str]
    matrix: Optional[List[List[Optional[float]]]]
    row_labels: List[str]
    col_labels: List[str]
    units: Dict[str, Optional[str]]
    axis_labels: Dict[str, Optional[str]]
    options: Dict[str, Any]
    output: Dict[str, Any]
    warnings: List[str]

    @property
    def ticker(self) -> str:
        return str(self.company.get("ticker") or "").upper()

    @property
    def company_name(self) -> str:
        return str(self.company.get("name") or self.ticker or "")

    def opt(self, key: str, default=None):
        return self.options.get(key, default)

    def left_series(self) -> List[Series]:
        return [s for s in self.series if s.axis == "left"]

    def right_series(self) -> List[Series]:
        return [s for s in self.series if s.axis == "right"]


def _as_list(v) -> List:
    if v is None:
        return []
    if isinstance(v, (list, tuple)):
        return list(v)
    return [v]


def _apply_derive(s: Series, all_series: Dict[str, Series], warnings: List[str]) -> None:
    d = s.derive or {}
    op = d.get("op")
    if not op:
        return
    if op not in fmt.DERIVATIONS:
        raise SpecError(f"series '{s.name}': unknown derive op '{op}'. "
                        f"Valid: {sorted(fmt.DERIVATIONS)}")
    if op in ("yoy", "qoq"):
        base = d.get("of")
        src_series = all_series.get(base) if base and base in all_series else s
        src = list(src_series.values)
        lag = 1 if op == "qoq" else int(d.get("periods_per_year", 4))
        prior = list(src_series.prior)[-lag:] if src_series.prior else []
        pad = len(prior)
        full = prior + src
        out = fmt.yoy(full, lag) if op == "yoy" else fmt.qoq(full)
        s.values = out[pad:]
        neg_base = sum(1 for i in range(max(pad, lag), len(full))
                       if full[i] is not None and full[i - lag] is not None and full[i - lag] < 0)
        if neg_base:
            warnings.append(
                f"series '{s.name}': derive.{op} left {neg_base} period(s) blank because the "
                f"prior value of '{src_series.name}' is negative; a percentage change from a "
                f"negative base inverts its meaning")
        if pad:
            warnings.append(
                f"series '{s.name}': derive.{op} used {pad} pre-window period(s) "
                f"of '{src_series.name}' history, so the line is complete across "
                f"the displayed window")
        elif lag:
            warnings.append(
                f"series '{s.name}': derive.{op} needs {lag} earlier period(s); "
                f"none supplied, so the first {min(lag, len(src))} point(s) are "
                f"undefined. Supply '{src_series.name}.prior' to fill them.")
        s.unit = s.unit or "pct"
    elif op == "margin":
        num, den = d.get("numerator"), d.get("denominator")
        if num not in all_series or den not in all_series:
            raise SpecError(f"series '{s.name}': margin derive needs 'numerator' and "
                            f"'denominator' naming other series in this spec")
        s.values = fmt.margin(all_series[num].values, all_series[den].values)
        s.unit = s.unit or "pct"
    elif op == "surprise_pct":
        a, e = d.get("actual"), d.get("estimate")
        if a not in all_series or e not in all_series:
            raise SpecError(f"series '{s.name}': surprise_pct derive needs 'actual' and "
                            f"'estimate' naming other series in this spec")
        s.values = fmt.surprise_pct(all_series[a].values, all_series[e].values)
        s.unit = s.unit or "pct"
    elif op == "rebase":
        s.values = fmt.rebase(s.values, float(d.get("base", 100.0)))
        s.unit = s.unit or "index"
    warnings.append(f"series '{s.name}' computed in-engine via derive.{op}")


def _handle_missing(s: Series, policy: str, warnings: List[str]) -> None:
    n_missing = sum(1 for v in s.values if v is None)
    if not n_missing:
        return
    if policy == "zero":
        s.values = [0.0 if v is None else v for v in s.values]
    elif policy == "interpolate":
        vals = list(s.values)
        for i, v in enumerate(vals):
            if v is not None:
                continue
            prev = next((vals[j] for j in range(i - 1, -1, -1) if vals[j] is not None), None)
            nxt = next((vals[j] for j in range(i + 1, len(vals)) if vals[j] is not None), None)
            if prev is not None and nxt is not None:
                vals[i] = (prev + nxt) / 2.0
        s.values = vals
    warnings.append(f"series '{s.name}': {n_missing} missing point(s), policy='{policy}'")


def parse(raw: Dict[str, Any]) -> ChartSpec:
    if not isinstance(raw, dict):
        raise SpecError("spec must be a JSON object")
    raw = copy.deepcopy(raw)
    warnings: List[str] = []

    chart_type = str(raw.get("chart_type") or "").strip().lower()
    if not chart_type:
        raise SpecError("spec.chart_type is required")

    company = raw.get("company") or {}
    if isinstance(company, str):
        company = {"ticker": company}
    if not isinstance(company, dict):
        raise SpecError("spec.company must be an object or a ticker string")

    periods = [str(p) for p in _as_list(raw.get("periods"))]
    categories = [str(c) for c in _as_list(raw.get("categories"))]

    restated = [p for p in periods if p.startswith("R-")]
    if restated:
        raise SpecError(
            f"periods contain un-normalised revision labels: {', '.join(restated[:4])}"
            + ("…" if len(restated) > 4 else "")
            + ". Strip the 'R-' prefix (it marks a restated period, not a different "
              "one) — see insync_charts.preflight.normalise_period. Passing raw "
              "labels through means lookups for the clean label have already missed.")

    options = dict(raw.get("options") or {})
    missing_policy = str(options.get("on_missing", "gap")).lower()
    if missing_policy not in VALID_MISSING:
        raise SpecError(f"options.on_missing must be one of {sorted(VALID_MISSING)}")

    raw_series = _as_list(raw.get("series"))
    raw_units = dict(raw.get("units") or {})
    named = [("units." + k, raw_units.get(k)) for k in ("left", "right")]
    named += [(f"series[{i}].unit", s.get("unit")) for i, s in enumerate(raw_series)
              if isinstance(s, dict)]
    for where, u in named:
        if u and u not in fmt.UNITS:
            raise SpecError(f"{where} '{u}' is not a known unit. Valid: {sorted(fmt.UNITS)}")
    for ax in ("left", "right"):
        raw_units.setdefault(ax, next((s.get("unit") for s in raw_series
                                       if isinstance(s, dict) and s.get("unit")
                                       and str(s.get("axis") or "left").lower() == ax), None))

    series: List[Series] = []
    for i, s in enumerate(raw_series):
        if not isinstance(s, dict):
            raise SpecError(f"series[{i}] must be an object")
        name = str(s.get("name") or f"Series {i + 1}")
        kind = str(s.get("kind") or "bar").lower()
        if kind not in VALID_KINDS:
            raise SpecError(f"series '{name}': kind '{kind}' invalid. Valid: {sorted(VALID_KINDS)}")
        axis = str(s.get("axis") or "left").lower()
        if axis not in VALID_AXES:
            raise SpecError(f"series '{name}': axis must be 'left' or 'right'")
        draw_unit = s.get("unit") or raw_units.get(axis)
        series.append(Series(
            name=name,
            values=_clean_values(_as_list(s.get("values")), draw_unit,
                                 f"series '{name}'", warnings),
            kind=kind, axis=axis,
            unit=s.get("unit"), color=s.get("color"),
            focus=bool(s.get("focus", False)),
            estimate_from=s.get("estimate_from"),
            labels=s.get("labels"), show_labels=s.get("show_labels"),
            alpha=s.get("alpha"), linewidth=s.get("linewidth"),
            zorder=s.get("zorder"), stack_group=s.get("stack_group"),
            derive=s.get("derive"),
            prior=_clean_values(_as_list(s.get("prior")), draw_unit,
                                f"series '{name}' prior", warnings),
            meta=dict(s.get("meta") or {}),
        ))

    by_name = {s.name: s for s in series}
    if len(by_name) != len(series):
        dupes = sorted({s.name for s in series} & {
            n for n in (x.name for x in series)
            if sum(1 for y in series if y.name == n) > 1})
        warnings.append(f"duplicate series names {dupes}: a 'derive' block naming one "
                        f"of these resolves to the last definition")
    for s in series:
        if s.derive:
            _apply_derive(s, by_name, warnings)

    xlen = len(periods) or len(categories)
    for s in series:
        if xlen and len(s.values) != xlen:
            if len(s.values) < xlen:
                warnings.append(f"series '{s.name}': padded {xlen - len(s.values)} missing "
                                f"trailing point(s) to match {xlen} x-values")
                s.values = s.values + [None] * (xlen - len(s.values))
            else:
                raise SpecError(f"series '{s.name}' has {len(s.values)} values but there are "
                                f"only {xlen} x-values; trim the series or add periods")
        _handle_missing(s, missing_policy, warnings)

    matrix = raw.get("matrix")
    if matrix is not None:
        matrix = [_clean_values(_as_list(r), None, f"matrix row {i + 1}", warnings)
                  for i, r in enumerate(_as_list(matrix))]
    row_labels = [str(r) for r in _as_list(raw.get("row_labels"))]
    col_labels = [str(c) for c in _as_list(raw.get("col_labels"))]
    if matrix:
        if row_labels and len(row_labels) != len(matrix):
            raise SpecError(f"row_labels ({len(row_labels)}) must match matrix rows ({len(matrix)})")
        widths = {len(r) for r in matrix}
        if len(widths) > 1:
            raise SpecError(f"matrix rows have inconsistent lengths: {sorted(widths)}")
        if col_labels and widths and len(col_labels) != widths.pop():
            raise SpecError("col_labels length must match matrix row width")

    if not series and not matrix:
        raise SpecError("spec needs at least one entry in 'series', or a 'matrix'")
    if series and not matrix and not periods and not categories:
        raise SpecError("spec needs 'periods' (or 'categories') for the x axis; "
                        "the list is missing or empty")

    units = dict(raw.get("units") or {})
    units.setdefault("left", next((s.unit for s in series if s.axis == "left" and s.unit), None))
    units.setdefault("right", next((s.unit for s in series if s.axis == "right" and s.unit), None))
    for s in series:
        if s.unit is None:
            s.unit = units.get(s.axis)

    axis_labels = dict(raw.get("axis_labels") or {})

    output = dict(raw.get("output") or {})
    formats = [str(f).lower().lstrip(".") for f in _as_list(output.get("formats")) or ["png", "svg"]]
    bad = [f for f in formats if f not in VALID_FORMATS]
    if bad:
        raise SpecError(f"output.formats {bad} invalid. Valid: {sorted(VALID_FORMATS)}")
    output["formats"] = formats
    pname = output.get("preset") or theme.DEFAULT_PRESET
    if pname not in theme.PRESETS:
        raise SpecError(f"output.preset '{pname}' unknown. Valid: {sorted(theme.PRESETS)}")
    output["preset"] = pname
    ticker = str(company.get("ticker") or "chart").upper()
    output.setdefault("basename", f"{ticker}_{chart_type}".replace(" ", "_"))
    base = str(output["basename"])
    if (not base.strip() or base.startswith(("/", "\\")) or os.path.isabs(base)
            or re.match(r"^[A-Za-z]:", base) or ".." in re.split(r"[\\/]", base)):
        raise SpecError(f"output.basename '{output['basename']}' must be a file name "
                        f"(optionally with sub-folders) inside the output folder")

    report_date = str(raw.get("report_date") or _dt.date.today().isoformat())
    source = str(raw.get("source") or "").strip()
    if not source:
        source = SOURCE_NOT_STATED
        warnings.append("no 'source' given; the chart says 'Source not stated'. "
                        "Set 'source' to the data provider")

    return ChartSpec(
        chart_type=chart_type, raw=raw, company=company,
        title=raw.get("title"), subtitle=raw.get("subtitle"),
        source=source,
        report_date=report_date, as_of=raw.get("as_of"),
        periods=periods, series=series, categories=categories,
        matrix=matrix, row_labels=row_labels, col_labels=col_labels,
        units=units, axis_labels=axis_labels, options=options,
        output=output, warnings=warnings,
    )
