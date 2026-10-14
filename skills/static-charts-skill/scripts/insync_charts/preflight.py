"""Caller-side pre-flight: turn raw MCP rows into series you can trust.

Static Charts is Path B — the caller fetches the data and the engine only draws
it. That split is deliberate, but it means the engine cannot see the two mistakes
that most reliably produce a wrong chart:

* **A period label that does not match.** The InSync MCP returns a restated
  quarter as ``R-1Q25``. Key a lookup on the raw string and it silently fails to
  match ``1Q25``, so a quarter that exists becomes a hole in the chart. The engine
  receives a clean period list and a ``None``, and has no way to tell that apart
  from data that is genuinely absent — it will faithfully mark ``n/a``, raise a
  coverage finding, and disclose a gap the caller invented.
* **Quarters that do not add up.** Four quarters summing to half the
  reported year is a 50% discrepancy. The engine has never seen the
  annual figure and cannot check it.

Both are easy to make. Use these helpers rather than
re-implementing them per caller:

    from insync_charts.preflight import normalise_rows, reconcile

    rows = normalise_rows(mcp_rows)                 # strips R-, keeps CY-
    values = [rows.get((tid, code, q)) for q in quarters]
    reconcile(values, reported_annual=54.1e9, label="revenue")

`reconcile` raises. That is the point: a series that does not add up must not
reach a chart, because once it is drawn every disclosure the engine prints will
describe the caller's error as though it were a property of the data.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

Number = Optional[float]


class PreflightError(AssertionError):
    pass


def normalise_period(label: str) -> str:
    s = str(label).strip()
    return s[2:] if s.startswith("R-") else s


def is_restated(label: str) -> bool:
    return str(label).strip().startswith("R-")


def normalise_rows(rows: Iterable[Dict[str, Any]],
                   period_key: str = "FISCALPERIOD",
                   ticker_key: str = "TICKER_ID",
                   code_key: str = "LTCODE",
                   value_key: str = "VALUE") -> Dict[Tuple[Any, Any, str], Any]:
    out: Dict[Tuple[Any, Any, str], Any] = {}
    for r in rows:
        key = (r.get(ticker_key), r.get(code_key),
               normalise_period(r.get(period_key, "")))
        out[key] = r.get(value_key)
    return out


def restated_periods(rows: Iterable[Dict[str, Any]],
                     period_key: str = "FISCALPERIOD") -> List[str]:
    seen, out = set(), []
    for r in rows:
        lbl = r.get(period_key, "")
        if is_restated(lbl):
            clean = normalise_period(lbl)
            if clean not in seen:
                seen.add(clean)
                out.append(clean)
    return out


def reconcile(quarters: Sequence[Number], reported_annual: Number,
              tol: float = 0.005, label: str = "series",
              periods: Optional[Sequence[str]] = None) -> float:
    if reported_annual in (None, 0):
        raise PreflightError(
            f"{label}: no reported annual figure supplied — there is nothing to "
            "reconcile against. Fetch the annual period and pass it in.")

    holes = [i for i, v in enumerate(quarters) if v is None]
    if holes:
        where = ", ".join(str(periods[i]) if periods and i < len(periods) else f"#{i}"
                          for i in holes)
        raise PreflightError(
            f"{label}: {where} missing — cannot reconcile. Before concluding the "
            "database is short, check for R- prefixed (restated) labels at source "
            "and for a period dropped by aggregation='quarterly'.")

    got = float(sum(quarters))
    gap = abs(got - reported_annual) / abs(reported_annual)
    if gap > tol:
        raise PreflightError(
            f"{label}: quarters sum to {got:,.6g} but the reported year is "
            f"{reported_annual:,.6g} — a {gap:.1%} gap. Do not chart this.")
    return got


def expect_rows(rows: Sequence[Any], expected: int, label: str = "fetch") -> None:
    if len(rows) != expected:
        raise PreflightError(
            f"{label}: got {len(rows)} rows, expected {expected}. A silently "
            "dropped period will be charted as a data gap that does not exist.")


__all__ = ["normalise_period", "is_restated", "normalise_rows",
           "restated_periods", "reconcile", "expect_rows", "PreflightError"]
