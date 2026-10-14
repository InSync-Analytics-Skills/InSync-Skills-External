"""InSync Analytics — static chart engine.

The single chart renderer for the InSync skills library. Import it, or shell out
to ``scripts/render_chart.py``. Never re-implement chart drawing in a calling skill.

    from insync_charts import render_chart
    render_chart({"chart_type": "revenue_trend", ...}, out_dir="charts/")
"""
from .preflight import (PreflightError, expect_rows,
                        normalise_period, normalise_rows, reconcile,
                        restated_periods)
from .render import (SpecError, catalogue, load_specs, render_chart,
                     render_figure, render_many, validate)
from .theme import token_map

__version__ = "1.9.1"
__all__ = ["render_chart", "render_figure", "render_many", "validate",
           "catalogue", "load_specs", "token_map", "SpecError", "__version__",
           "normalise_period", "normalise_rows", "restated_periods",
           "reconcile", "expect_rows", "PreflightError"]
