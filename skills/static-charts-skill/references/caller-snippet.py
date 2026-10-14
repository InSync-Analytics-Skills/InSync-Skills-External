"""Canonical resolver for calling skills. Copy this verbatim — do not re-invent it.

A calling skill (earnings-preview, earnings-wrap, tearsheet, capital-allocation,
…) needs the path to this engine's `scripts/` directory. Skills install as
siblings under one skills root, so the engine sits next to the caller:

    skills/
      earnings-wrap/scripts/build_wrap.py    <- the caller
      static-charts/scripts/render_chart.py  <- this engine

Verified working from a sibling skill directory, via both the Python import and
the CLI. `INSYNC_CHARTS_SCRIPTS` overrides everything, for unusual installs.
"""
from __future__ import annotations

import os
from typing import Optional

ENGINE_DIRS = ("static-charts-skill", "static-charts", "insync-static-charts")


def find_chart_engine(start: Optional[str] = None) -> str:
    env = os.environ.get("INSYNC_CHARTS_SCRIPTS")
    if env and os.path.exists(os.path.join(env, "render_chart.py")):
        return env

    here = os.path.dirname(os.path.abspath(start or __file__))
    for base in (here, os.path.dirname(here)):
        for name in ENGINE_DIRS:
            p = os.path.join(os.path.dirname(base), name, "scripts")
            if os.path.exists(os.path.join(p, "render_chart.py")):
                return p
    raise RuntimeError(
        "The static-charts skill was not found next to this one. Install it, or "
        "set INSYNC_CHARTS_SCRIPTS to its scripts/ directory.")


def render(specs, out_dir: str, *, strict: bool = False) -> dict:
    import sys
    try:
        scripts = find_chart_engine()
    except RuntimeError as e:
        return {"count": 0, "rendered": 0, "failed": 0, "charts": [],
                "engine_error": str(e)}
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    from insync_charts import render_chart, render_many
    if isinstance(specs, dict):
        return {"count": 1, "charts": [render_chart(specs, out_dir, strict=strict)],
                "rendered": 1, "failed": 0}
    return render_many(specs, out_dir, strict=strict)


if __name__ == "__main__":
    print(find_chart_engine())
