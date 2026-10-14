"""Chart-type registry.

Each supported chart type binds a primitive to a set of house defaults: the
fallback title, the subtitle pattern, the default unit, and any option overrides.
Adding a chart type means adding a row here — never a new drawing routine, unless
a genuinely new visual grammar is required.
"""
from __future__ import annotations

from typing import Any, Dict, List

REGISTRY: Dict[str, Dict[str, Any]] = {}


def _reg(key: str, primitive: str, title: str, *, subtitle: str = "",
         subtitle_single: str = "", unit: str = None, options: Dict[str, Any] = None,
         requires: str = "series", family: str = "") -> None:
    REGISTRY[key] = {
        "primitive": primitive, "title": title, "subtitle": subtitle,
        "subtitle_single": subtitle_single or subtitle,
        "unit": unit, "options": dict(options or {}), "requires": requires,
        "family": family,
    }


_reg("revenue_trend", "combo_trend", "Revenue trend",
     subtitle="Quarterly revenue and year-on-year growth",
     subtitle_single="Quarterly revenue", unit="usd_mn",
     family="Financial performance")
_reg("ebitda_trend", "combo_trend", "EBITDA trend",
     subtitle="Adjusted EBITDA and margin", subtitle_single="Adjusted EBITDA",
     unit="usd_mn", family="Financial performance")
_reg("eps_trend", "combo_trend", "EPS trend",
     subtitle="Adjusted diluted EPS", unit="usd_ps", family="Financial performance")
_reg("margin_trend", "combo_trend", "Margin trend",
     subtitle="Reported margins by quarter", unit="pct",
     options={"data_labels": "auto"}, family="Financial performance")
_reg("metric_trend", "combo_trend", "Metric trend", family="Financial performance")
_reg("kpi_trend", "combo_trend", "KPI trend", family="Financial performance")
_reg("segment_revenue_mix", "stacked_bar", "Segment revenue mix",
     subtitle="Revenue by reporting segment", unit="usd_mn",
     options={"normalise": False}, family="Financial performance")
_reg("segment_mix", "stacked_bar", "Segment mix", unit="pct",
     options={"normalise": True}, family="Financial performance")
_reg("fcf_trend", "combo_trend", "Free cash flow trend", unit="usd_mn",
     family="Financial performance")

_reg("estimate_revisions", "combo_trend", "Estimate revisions",
     subtitle="Consensus estimate path", options={"data_labels": False},
     family="Consensus & revisions")
_reg("revision_trend", "combo_trend", "Estimate revision trend",
     subtitle="Consensus evolution over the revision window",
     options={"data_labels": False}, family="Consensus & revisions")
_reg("consensus_trend", "combo_trend", "Consensus trend",
     subtitle="Consensus versus reported", family="Consensus & revisions")
_reg("surprise_history", "surprise", "Earnings surprise history",
     subtitle="Reported versus consensus, by quarter", unit="pct",
     family="Consensus & revisions")
_reg("forecast_accuracy", "surprise", "Forecast accuracy history",
     subtitle="Realised outcome versus prior-period consensus", unit="pct",
     family="Consensus & revisions")
_reg("guidance_vs_consensus", "combo_trend", "Guidance versus consensus",
     family="Consensus & revisions")

_reg("pe_history", "band_history", "P/E history",
     subtitle="Forward P/E with period median and P10–P90 band", unit="x", family="Valuation")
_reg("ev_ebitda_history", "band_history", "EV/EBITDA history",
     subtitle="Forward EV/EBITDA with period median and P10–P90 band", unit="x", family="Valuation")
_reg("ev_sales_history", "band_history", "EV/Sales history",
     subtitle="Forward EV/Sales with period median and P10–P90 band", unit="x", family="Valuation")
_reg("valuation_history", "band_history", "Valuation history",
     subtitle="Trading multiple with period median and P10–P90 band", unit="x", family="Valuation")
_reg("relative_valuation", "scatter_xy", "Relative valuation versus peers",
     subtitle="Valuation against growth", family="Valuation",
     options={"quadrants": True})

_reg("peer_comparison", "peer_bar", "Peer comparison",
     subtitle="Subject company versus peer set", requires="categories",
     family="Peer analysis")
_reg("peer_ranking", "peer_bar", "Peer ranking", requires="categories",
     family="Peer analysis")
_reg("peer_growth_comparison", "peer_bar", "Revenue growth versus peers",
     subtitle="Consensus revenue growth", unit="pct", requires="categories",
     family="Peer analysis")
_reg("peer_margin_comparison", "peer_bar", "Margin comparison versus peers",
     unit="pct", requires="categories", family="Peer analysis")
_reg("peer_valuation_comparison", "peer_bar", "Valuation comparison versus peers",
     unit="x", requires="categories", family="Peer analysis")

_reg("capital_allocation_waterfall", "waterfall", "Capital allocation",
     subtitle="Sources and uses of cash", unit="usd_mn", requires="categories",
     family="Capital allocation")
_reg("capital_deployment_bridge", "waterfall", "Capital deployment bridge",
     unit="usd_mn", requires="categories", family="Capital allocation")
_reg("cash_flow_waterfall", "waterfall", "Cash flow allocation",
     unit="usd_mn", requires="categories", family="Capital allocation")
_reg("shareholder_returns", "stacked_bar", "Shareholder returns",
     subtitle="Buybacks and dividends", unit="usd_mn", family="Capital allocation")
_reg("capex_trend", "combo_trend", "Capex trend",
     subtitle="Capital expenditure and intensity",
     subtitle_single="Capital expenditure", unit="usd_mn",
     family="Capital allocation")

_reg("revision_heatmap", "heatmap", "Estimate revision heatmap",
     subtitle="Change in consensus, by name and horizon", unit="pct",
     requires="matrix", options={"color_scheme": "diverging"},
     family="Screening & monitoring")
_reg("ranking_heatmap", "heatmap", "Ranking heatmap", requires="matrix",
     options={"color_scheme": "sequential"}, family="Screening & monitoring")
_reg("relative_performance", "combo_trend", "Relative performance",
     subtitle="Total return, rebased to 100", unit="index",
     options={"data_labels": False}, family="Screening & monitoring")


ALIASES = {
    "revenue": "revenue_trend", "sales_trend": "revenue_trend",
    "eps": "eps_trend", "ebitda": "ebitda_trend",
    "margins": "margin_trend", "margin": "margin_trend",
    "pe": "pe_history", "p/e_history": "pe_history",
    "ev/ebitda_history": "ev_ebitda_history", "ev/sales_history": "ev_sales_history",
    "surprise": "surprise_history", "beat_history": "surprise_history",
    "peer_comps": "peer_comparison", "peer_bar": "peer_comparison",
    "waterfall": "capital_allocation_waterfall",
    "capital_allocation": "capital_allocation_waterfall",
    "revisions": "estimate_revisions",
    "heatmap": "revision_heatmap",
    "rel_perf": "relative_performance",
}


def resolve(chart_type: str) -> Dict[str, Any]:
    key = chart_type.strip().lower().replace(" ", "_").replace("-", "_")
    key = ALIASES.get(key, key)
    if key not in REGISTRY:
        raise KeyError(
            f"unknown chart_type '{chart_type}'. Supported: {', '.join(sorted(REGISTRY))}")
    out = dict(REGISTRY[key])
    out["key"] = key
    return out


def catalogue() -> List[Dict[str, str]]:
    return [
        {"chart_type": k, "family": v["family"], "primitive": v["primitive"],
         "default_title": v["title"], "requires": v["requires"]}
        for k, v in sorted(REGISTRY.items(), key=lambda kv: (kv[1]["family"], kv[0]))
    ]
