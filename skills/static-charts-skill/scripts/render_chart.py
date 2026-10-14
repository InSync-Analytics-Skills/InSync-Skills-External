#!/usr/bin/env python3
"""InSync static chart engine — command line entry point.

    python render_chart.py --spec spec.json --out-dir charts/
    cat spec.json | python render_chart.py --spec - --out-dir charts/
    python render_chart.py --list
    python render_chart.py --spec spec.json --validate-only

The spec file may contain a single chart object, a list of chart objects, or
``{"company": {...}, "source": "...", "charts": [ ... ]}`` where the top-level
keys are merged into every chart (useful for a whole report's worth of charts).

A JSON manifest is printed to stdout. Exit code is 0 only if every chart
rendered. Nothing is ever written outside --out-dir.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from insync_charts import (SpecError, catalogue, load_specs,
                           render_many, validate, __version__)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="render_chart.py",
        description="Render institutional static charts from a JSON chart spec.")
    ap.add_argument("--spec", help="path to a JSON spec file, or '-' for stdin")
    ap.add_argument("--out-dir", default="charts", help="output directory (default: charts)")
    ap.add_argument("--format", help="override output formats, e.g. png,svg")
    ap.add_argument("--preset", help="override the size preset, e.g. word, slide, web")
    ap.add_argument("--validate-only", action="store_true",
                    help="parse and check the spec without drawing")
    ap.add_argument("--list", action="store_true", help="print the chart-type catalogue")
    ap.add_argument("--tokens", action="store_true", help="print the design tokens as JSON")
    ap.add_argument("--strict", action="store_true",
                    help="abort the batch on the first failure")
    ap.add_argument("--quiet", action="store_true", help="manifest only, no pretty printing")
    ap.add_argument("--version", action="version", version=f"insync-charts {__version__}")
    args = ap.parse_args(argv)

    if args.list:
        rows = catalogue()
        if args.quiet:
            print(json.dumps(rows, indent=2))
        else:
            fam = None
            for r in rows:
                if r["family"] != fam:
                    fam = r["family"]
                    print(f"\n{fam}")
                print(f"  {r['chart_type']:<32} {r['primitive']:<14} requires={r['requires']}")
            print()
        return 0

    if args.tokens:
        from insync_charts import token_map
        print(json.dumps(token_map(), indent=2))
        return 0

    if not args.spec:
        ap.error("--spec is required (or use --list / --tokens)")

    try:
        specs = load_specs(args.spec)
    except (OSError, json.JSONDecodeError, SpecError) as e:
        print(json.dumps({"ok": False, "error": f"could not read spec: {e}"}))
        return 2

    for sp in specs:
        if args.format:
            sp.setdefault("output", {})["formats"] = [
                f.strip().lstrip(".") for f in args.format.split(",") if f.strip()]
        if args.preset:
            sp.setdefault("output", {})["preset"] = args.preset

    if args.validate_only:
        results = [dict(validate(sp), index=i) for i, sp in enumerate(specs)]
        ok = all(r["ok"] for r in results)
        print(json.dumps({"ok": ok, "count": len(results), "results": results}, indent=2))
        return 0 if ok else 1

    manifest = render_many(specs, args.out_dir, strict=args.strict)
    print(json.dumps(manifest, indent=2))
    if not args.quiet and manifest["failed"]:
        for c in manifest["charts"]:
            if not c.get("ok"):
                print(f"FAILED {c.get('chart_type')}: {c.get('error')}", file=sys.stderr)
    return 0 if manifest["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
