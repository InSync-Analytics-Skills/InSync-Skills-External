#!/usr/bin/env python3
"""Turn raw InSync MCP rows into a chart spec — without retyping numbers.

This is the bridge between data collection and drawing. The agent saves whatever
the MCP returned to a JSON file; this script reshapes it. The agent never
transcribes a figure by hand, which is the only way to guarantee the chart shows
what the database actually said.

    python build_spec.py --raw raw.json --map map.json --out spec.json
    python render_chart.py --spec spec.json --out-dir charts/

`map.json` describes where the numbers live, for example:

{
  "chart_type": "revenue_trend",
  "company": {"name": "Example Software Co", "ticker": "EXSW", "sector": "Software"},
  "source": "InSync MCP",
  "rows_path": "data.rows",
  "period_field": "fiscal_period",
  "series": [
    {"name": "Revenue", "value_field": "value", "where": {"metric": "SALES"},
     "kind": "bar", "unit": "usd_mn"},
    {"name": "YoY growth", "kind": "line", "axis": "right",
     "derive": {"op": "yoy", "of": "Revenue"}}
  ]
}

Everything in `map.json` other than `rows_path`, `period_field`, `value_field`
and `where` is copied straight through to the chart spec.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Dict, Iterable, List, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from insync_charts import SpecError, validate


def dig(obj: Any, path: Optional[str]) -> Any:
    if not path:
        return obj
    cur = obj
    for part in path.split("."):
        if isinstance(cur, dict):
            cur = cur.get(part)
        elif isinstance(cur, list):
            cur = next((d.get(part) for d in cur
                        if isinstance(d, dict) and part in d), None)
        else:
            return None
    return cur


def find_rows(payload: Any, rows_path: Optional[str]) -> List[Dict[str, Any]]:
    if rows_path:
        rows = dig(payload, rows_path)
        if not isinstance(rows, list):
            raise SpecError(f"rows_path '{rows_path}' did not resolve to a list")
        return [r for r in rows if isinstance(r, dict)]
    best: List[Dict[str, Any]] = []
    stack = [payload]
    while stack:
        cur = stack.pop()
        if isinstance(cur, list):
            dicts = [x for x in cur if isinstance(x, dict)]
            if len(dicts) > len(best):
                best = dicts
            stack.extend(x for x in cur if isinstance(x, (list, dict)))
        elif isinstance(cur, dict):
            stack.extend(cur.values())
    if not best:
        raise SpecError("no list of records found in the raw payload; set 'rows_path'")
    return best


def matches(row: Dict[str, Any], where: Dict[str, Any]) -> bool:
    for k, want in (where or {}).items():
        got = row.get(k)
        if isinstance(want, list):
            if got not in want:
                return False
        elif str(got) != str(want):
            return False
    return True


def clean_key(value: Any, strip: List[str]) -> str:
    s = str(value)
    for p in strip:
        if s.startswith(p):
            s = s[len(p):]
    return s


def ordered_periods(rows: Iterable[Dict[str, Any]], field: str,
                    explicit: Optional[List[str]], strip: List[str]) -> List[str]:
    if explicit:
        return [str(p) for p in explicit]
    seen: List[str] = []
    for r in rows:
        p = r.get(field)
        if p is None:
            continue
        p = clean_key(p, strip)
        if p not in seen:
            seen.append(p)
    return seen


def build(raw: Any, mapping: Dict[str, Any]) -> Dict[str, Any]:
    rows = find_rows(raw, mapping.get("rows_path"))
    period_field = mapping.get("period_field")
    category_field = mapping.get("category_field")
    key_field = period_field or category_field
    if not key_field:
        raise SpecError("map needs 'period_field' (time series) or "
                        "'category_field' (peer / bridge charts)")

    strip = [str(p) for p in (mapping.get("period_strip") or ["R-"])]
    keys = ordered_periods(rows, key_field,
                           mapping.get("periods") or mapping.get("categories"), strip)

    spec: Dict[str, Any] = {k: v for k, v in mapping.items()
                            if k not in {"rows_path", "period_field", "category_field",
                                         "series", "matrix_from", "period_strip"}}
    if period_field:
        spec["periods"] = keys
    else:
        spec["categories"] = keys

    out_series = []
    for sm in mapping.get("series", []):
        sm = dict(sm)
        vf = sm.pop("value_field", None)
        where = sm.pop("where", None)
        if vf:
            lookup: Dict[str, Any] = {}
            for r in rows:
                if where and not matches(r, where):
                    continue
                k = clean_key(r.get(key_field), strip)
                if k in lookup:
                    continue
                lookup[k] = r.get(vf)
            sm["values"] = [lookup.get(k) for k in keys]
        elif "values" not in sm and not sm.get("derive"):
            raise SpecError(f"series '{sm.get('name')}' needs 'value_field', "
                            f"explicit 'values', or a 'derive' block")
        out_series.append(sm)
    spec["series"] = out_series

    mf = mapping.get("matrix_from")
    if mf:
        rlab = ordered_periods(rows, mf["row_field"], mf.get("row_labels"), strip)
        clab = ordered_periods(rows, mf["col_field"], mf.get("col_labels"), strip)
        cell = {(str(r.get(mf["row_field"])), str(r.get(mf["col_field"]))):
                r.get(mf["value_field"]) for r in rows}
        spec["row_labels"] = rlab
        spec["col_labels"] = clab
        spec["matrix"] = [[cell.get((rl, cl)) for cl in clab] for rl in rlab]
        spec.pop("series", None) if not out_series else None

    return spec


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--raw", required=True, help="JSON file of raw MCP rows")
    ap.add_argument("--map", required=True, help="JSON file describing the mapping")
    ap.add_argument("--out", help="write the spec here (default: stdout)")
    ap.add_argument("--no-validate", action="store_true")
    args = ap.parse_args(argv)

    with open(args.raw, "r", encoding="utf-8") as fh:
        raw = json.load(fh)
    with open(args.map, "r", encoding="utf-8") as fh:
        mapping = json.load(fh)

    try:
        spec = build(raw, mapping)
    except SpecError as e:
        print(json.dumps({"ok": False, "error": str(e)}), file=sys.stderr)
        return 2

    if not args.no_validate:
        v = validate(spec)
        if not v["ok"]:
            print(json.dumps({"ok": False, "error": v["error"], "spec": spec},
                             indent=2), file=sys.stderr)
            return 1
        filled = sum(1 for s in spec.get("series", [])
                     for x in s.get("values", []) if x is not None)
        print(json.dumps({"ok": True, "chart_type": spec.get("chart_type"),
                          "x_values": len(spec.get("periods") or spec.get("categories") or []),
                          "series": len(spec.get("series", [])),
                          "data_points": filled,
                          "warnings": v["warnings"]}), file=sys.stderr)

    text = json.dumps(spec, indent=2)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
